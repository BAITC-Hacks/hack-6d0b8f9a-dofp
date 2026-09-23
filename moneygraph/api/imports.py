"""Local file upload and background calculation using role B's existing pipeline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .service import QueryService

FILES = {'nodes.parquet': 10000, 'edges.parquet': 50000, 'transactions.parquet': 200000}
MAX_BYTES = 40 * 1024 * 1024


class ImportSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    period_start: date
    period_end: date


class ImportManager:
    def __init__(self, publish, root=None, runner=None):
        self.publish = publish
        self.root = Path(root) if root else Path.cwd() / 'out' / 'imports'
        self.runner = runner
        self.lock = Lock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='moneygraph-import')

    def begin(self, spec):
        if spec.period_end < spec.period_start:
            raise HTTPException(422, 'Дата окончания должна быть не раньше даты начала.')
        with self.lock:
            for job in self.jobs.values():
                if job['state'] in ('uploading', 'writing') and monotonic()-job['created'] > 900:
                    job['state'] = 'failed'
                    job['message'] = 'Время загрузки истекло. Начните заново.'
                    job['temp'].cleanup()
                if job['state'] in ('uploading', 'writing', 'running'):
                    raise HTTPException(409, 'Уже выполняется загрузка или расчёт. Дождитесь завершения.')
            while len(self.jobs) >= 4:
                self.jobs.pop(next(iter(self.jobs)))
            self.root.mkdir(parents=True, exist_ok=True)
            job_id = uuid4().hex
            temporary = TemporaryDirectory(prefix='.upload-', dir=self.root)
            self.jobs[job_id] = {'id': job_id, 'state': 'uploading', 'files': [], 'message': 'Выберите три файла.',
                'created': monotonic(), 'temp': temporary, 'spec': spec, 'run_id': None}
            return self.status(job_id)

    def job(self, job_id):
        if job_id not in self.jobs:
            raise HTTPException(404, 'Загрузка не найдена. Начните заново.')
        return self.jobs[job_id]

    def status(self, job_id):
        job = self.job(job_id)
        return {k: job[k] for k in ('id', 'state', 'files', 'message', 'run_id')}

    async def upload(self, job_id, name, request):
        if name not in FILES:
            raise HTTPException(422, 'Нужны nodes.parquet, edges.parquet и transactions.parquet.')
        with self.lock:
            job = self.job(job_id)
            if job['state'] != 'uploading':
                raise HTTPException(409, 'Эта загрузка больше не принимает файлы.')
            job['state'] = 'writing'
        target = Path(job['temp'].name) / name
        size = 0
        try:
            with target.open('wb') as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, 'Размер одного файла не должен превышать 40 МБ.')
                    output.write(chunk)
            import pyarrow.parquet as pq
            try:
                metadata = pq.read_metadata(target)
                if metadata.num_rows > FILES[name]:
                    raise HTTPException(413, f'Слишком много строк в {name}. Лимит: {FILES[name]}.')
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(422, f'{name}: файл не читается как Parquet. Выберите исходный файл данных.') from exc
            with self.lock:
                job['files'] = sorted(set(job['files']) | {name})
                job['state'] = 'uploading'
            return self.status(job_id)
        except BaseException:
            with self.lock:
                job['state'] = 'failed'
                job['message'] = 'Не удалось загрузить файл. Начните загрузку заново.'
                job['temp'].cleanup()
            raise

    def start(self, job_id):
        with self.lock:
            job = self.job(job_id)
            if job['state'] != 'uploading':
                raise HTTPException(409, 'Расчёт уже запущен или загрузка завершилась ошибкой.')
            if set(job['files']) != set(FILES):
                raise HTTPException(422, 'Сначала загрузите все три файла.')
            job.update(state='running', message='Проверяем данные и рассчитываем связи и роли…')
            self.pool.submit(self.calculate, job)
        return self.status(job_id)

    def calculate(self, job):
        try:
            from moneygraph.config import PipelineConfig
            from moneygraph.pipeline import run_pipeline
            spec = job['spec']
            snapshot = (self.runner or run_pipeline)(Path(job['temp'].name), self.root,
                config=PipelineConfig(period_start=spec.period_start, period_end=spec.period_end))
            service = QueryService(snapshot)
            self.publish(service)
            with self.lock:
                job.update(state='complete', run_id=service.run_id, message='Расчёт готов. Открываем результаты.')
        except Exception as exc:
            # A bad upload never replaces the active, verified calculation.
            detail = str(exc)
            if 'outside reporting period' in detail or 'after period_end' in detail:
                message = 'В файлах есть переводы вне выбранного периода. Исправьте даты и загрузите заново.'
            elif 'amount' in detail or 'count' in detail or 'pairs differ' in detail:
                message = 'Файлы не согласованы: суммы, пары клиентов или число операций различаются. Выберите три файла одного набора.'
            elif isinstance(exc, (ValueError, OSError)):
                message = 'Данные не прошли проверку. Проверьте обязательные столбцы, номера клиентов, даты и суммы. Предыдущий расчёт сохранён.'
            else:
                message = 'Расчёт не завершился. Проверьте установку зависимостей проекта. Предыдущий расчёт сохранён.'
            with self.lock:
                job.update(state='failed', message=message)
        finally:
            job['temp'].cleanup()

    def close(self):
        self.pool.shutdown(wait=True)
        for job in self.jobs.values():
            job['temp'].cleanup()


def import_router(manager):
    router = APIRouter(prefix='/api/v1/imports')

    def local_request(request):
        # Reject cross-site forms/fetch and DNS rebinding; no CORS is enabled.
        from urllib.parse import urlsplit
        host = urlsplit(str(request.url)).hostname
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise HTTPException(403, 'Загрузка доступна только через локальный адрес.')
        if request.headers.get('x-moneygraph-client') != 'local-ui':
            raise HTTPException(403, 'Загрузите файлы через интерфейс приложения.')
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
            raise HTTPException(403, 'Загрузка с другого сайта запрещена.')

    @router.post('', status_code=201)
    def begin(spec: ImportSpec, request: Request):
        local_request(request)
        return manager.begin(spec)

    @router.put('/{job_id}/files/{name}')
    async def upload(job_id: str, name: str, request: Request):
        local_request(request)
        return await manager.upload(job_id, name, request)

    @router.post('/{job_id}/run', status_code=202)
    def start(job_id: str, request: Request):
        local_request(request)
        return manager.start(job_id)

    @router.get('/{job_id}')
    def status(job_id: str):
        return manager.status(job_id)

    return router
