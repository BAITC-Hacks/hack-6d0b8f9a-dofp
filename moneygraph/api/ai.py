"""Optional AI boundary. Normal API startup never depends on AI or its provider."""
import os
from threading import RLock
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from .imports import local_request
from .snapshot import SCHEMA


MESSAGES = {
    'hidden': 'AI скрыт настройкой сервера.',
    'disabled': 'AI выключен. Включите его в настройках запуска сервера.',
    'configuration': 'Проверьте настройки AI на сервере.',
    'missing_api_key': 'На сервере не задан API-ключ. Ключ вводится в терминале, не в браузере.',
    'external_transfer_disabled': 'Внешняя передача выключена в настройках сервера.',
    'invalid_context': 'AI нужен завершённый проверенный расчёт. Загрузите Parquet или откройте его снимок.',
    'context_changed': 'Файлы расчёта изменились после открытия. Перезапустите сервер с проверенным снимком.',
    'busy': 'AI уже обрабатывает запрос. Дождитесь завершения и повторите.',
    'rate_limited': 'Провайдер ограничил запросы. Проверьте квоту или повторите позже.',
    'authentication_failed': 'Провайдер отклонил ключ. Проверьте его на сервере.',
    'access_denied': 'У API-проекта нет доступа к этому запросу.',
    'model_or_endpoint_unavailable': 'Настроенная модель недоступна для API-проекта.',
    'timeout': 'AI не успел ответить. Обычные объяснения и операции доступны.',
    'unsupported_claim': 'Записка не прошла проверку фактов и не показана. Используйте обычное объяснение.',
    'invalid_response': 'Ответ AI не прошёл проверку формата или фактов и не показан.',
    'incomplete_response': 'Ответ AI оборвался. Попробуйте ещё раз.',
    'response_too_large': 'Ответ AI превысил допустимый размер.',
    'request_too_large': 'Для этой карточки пакет фактов слишком велик.',
    'provider_unavailable': 'Не удалось получить ответ AI. Обычное расследование продолжает работать.',
    'unavailable': 'AI временно недоступен. Обычное расследование продолжает работать.',
}


def ai_ui_enabled():
    return os.environ.get('MONEYGRAPH_AI_UI_ENABLED', 'true').lower() == 'true'


class EmptyAIRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


def ai_router(current, require_node):
    router = APIRouter()
    builders = WeakKeyDictionary()
    lock = RLock()

    def configured(svc):
        from moneygraph.ai import AIConfig, AIUnavailable
        if not ai_ui_enabled():
            raise AIUnavailable('hidden')
        config = AIConfig.from_env()
        config.endpoint()
        if svc.schema_version != SCHEMA or svc.snapshot_directory is None:
            raise AIUnavailable('invalid_context')
        return config

    def context(svc, gid):
        from moneygraph.ai import SnapshotContextBuilder, AIUnavailable
        with lock:
            builder = builders.get(svc)
            if builder is None:
                builder = SnapshotContextBuilder(svc.snapshot_directory)
                if (builder.run_id != svc.run_id or
                        builder.artifact_hashes != svc.manifest.get('artifacts')):
                    raise AIUnavailable('context_changed')
                builders[svc] = builder
        return builder.for_client(gid)

    def safe_error(exc):
        code = getattr(exc, 'code', 'unavailable')
        code = code if code in MESSAGES else 'unavailable'
        return {'code': code, 'message': MESSAGES[code]}

    @router.get('/api/v1/runs/{run_id}/nodes/{gid}/ai-status')
    def status(run_id: str, gid: str, response: Response):
        svc = current(run_id)
        require_node(svc, gid)
        response.headers['Cache-Control'] = 'no-store'
        try:
            config = configured(svc)
            data = {'ready': True, 'code': 'ready', 'message': 'Готов к запросу',
                    'provider': config.provider, 'model': config.model,
                    'external': urlsplit(config.endpoint()).hostname not in ('127.0.0.1', '::1')}
        except Exception as exc:
            data = {'ready': False, **safe_error(exc)}
        return svc.envelope({'visible': ai_ui_enabled(), **data})

    @router.post('/api/v1/runs/{run_id}/nodes/{gid}/ai-explanation')
    def explain(run_id: str, gid: str, request: Request, response: Response, payload: EmptyAIRequest | None = None):
        local_request(request)
        svc = current(run_id)
        normalized = require_node(svc, gid)
        response.headers['Cache-Control'] = 'no-store'
        try:
            from moneygraph.ai import explain_client, InvalidContext, AIUnavailable
            configured(svc)
            try:
                facts = context(svc, normalized)
            except InvalidContext:
                raise AIUnavailable('invalid_context') from None
            result = explain_client(facts)
            if result['metadata']['snapshot_id'] != run_id:
                raise AIUnavailable('invalid_response')
        except Exception as exc:
            error = safe_error(exc)
            raise HTTPException(429 if error['code'] in ('busy', 'rate_limited') else 503,
                                error, headers={'Cache-Control': 'no-store'}) from None
        return svc.envelope({**result, 'client_id': normalized})

    return router
