from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .models import Role, gid_string
from .service import EXPORT_COLUMNS, QueryService


def create_app(snapshot: Any = None, *, data_dir: str | Path | None = None, static_dir: str | Path | None = None) -> FastAPI:
    """Create a read-only API from a snapshot mapping/dataclass/model or directory."""
    service = QueryService(snapshot, Path(data_dir) if data_dir else None) if snapshot is not None else None
    app = FastAPI(title='Граф денег · Investigation API', version='1.0.0', description='Чтение готового расчёта. API не назначает роли и не изменяет исходные данные.')
    app.state.query_service = service

    def current(run_id: str | None = None) -> QueryService:
        if service is None:
            raise HTTPException(503, {'code': 'snapshot_not_loaded', 'message': 'Нет подключённого расчёта. Передайте готовый снимок через --snapshot.'})
        if run_id is not None and run_id != service.run_id:
            raise HTTPException(404, {'code': 'run_not_found', 'message': 'Этот расчёт не загружен. Обновите страницу для выбора текущей версии.'})
        return service

    def require_node(svc: QueryService, gid: str) -> str:
        try:
            normalized = gid_string(gid)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if normalized not in svc.nodes:
            raise HTTPException(404, {'code': 'node_not_found', 'message': f'Узел {normalized} отсутствует в расчёте.'})
        return normalized

    @app.get('/health')
    def health():
        svc = current()
        return {'status': 'ready', 'run_id': svc.run_id}

    @app.get('/api/v1/runs/current')
    def get_current():
        svc = current()
        return svc.envelope(svc.summary())

    @app.get('/api/v1/runs/{run_id}/nodes')
    def list_nodes(run_id: str, role: Role | None = None, cluster_id: Annotated[int | None, Query(ge=0)] = None, seed_only: bool = False, q: Annotated[str | None, Query(max_length=20)] = None, limit: Annotated[int, Query(ge=1, le=500)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
        svc = current(run_id)
        return svc.envelope(svc.list_nodes(role, cluster_id, seed_only, q, limit, offset))

    @app.get('/api/v1/runs/{run_id}/nodes/{gid}')
    def get_node(run_id: str, gid: str):
        svc = current(run_id)
        return svc.envelope(svc.node(require_node(svc, gid)))

    @app.get('/api/v1/runs/{run_id}/graph')
    def get_graph(run_id: str, gid: str | None = None, hops: Annotated[int, Query(ge=0, le=2)] = 1, limit: Annotated[int, Query(ge=1, le=500)] = 250, cluster_id: Annotated[int | None, Query(ge=0)] = None):
        svc = current(run_id)
        normalized = require_node(svc, gid) if gid is not None else None
        return svc.envelope(svc.graph(normalized, hops, limit, cluster_id))

    @app.get('/api/v1/runs/{run_id}/clusters')
    def get_clusters(run_id: str):
        svc = current(run_id)
        return svc.envelope({'items': svc.clusters, 'total': len(svc.clusters)})

    @app.get('/api/v1/runs/{run_id}/nodes/{gid}/transactions')
    def get_transactions(run_id: str, gid: str, limit: Annotated[int, Query(ge=1, le=500)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
        svc = current(run_id)
        return svc.envelope(svc.node_transactions(require_node(svc, gid), limit, offset))

    @app.get('/api/v1/runs/{run_id}/exports/{name}')
    def export(run_id: str, name: str):
        svc = current(run_id)
        if name not in EXPORT_COLUMNS:
            raise HTTPException(404, 'Unknown export')
        return Response(content=svc.exports[name], media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{name}"', 'X-Run-Id': svc.run_id, 'Cache-Control': 'no-store'})

    # Only / is used by the frontend. Unknown /api routes never become HTML successes.
    @app.api_route('/api/{unmatched:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    def api_not_found(unmatched: str):
        raise HTTPException(404, 'Unknown API route')

    build = Path(static_dir) if static_dir else Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if static_dir is not None and not (build / 'index.html').is_file():
        raise ValueError('Frontend build not found: --static-dir must contain index.html and assets')
    if (build / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=build, html=True), name='frontend')
    else:
        @app.get('/')
        def frontend_unavailable():
            raise HTTPException(503, {'code': 'frontend_not_found', 'message': 'Frontend build is not installed. Run from the repository or pass --static-dir PATH/frontend/dist.'})
    return app
