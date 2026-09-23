"""Optional check against C's existing app, without editing C's files.

The example route below belongs to the test only; C owns the production route.
"""
import os

import pytest

from moneygraph.ai import AIUnavailable, ExplanationService, SnapshotContextBuilder
from conftest import FakeTransport


@pytest.mark.parametrize("failure", ["disabled", "provider_unavailable", "invalid_response"])
def test_existing_api_survives_ai_failure(snapshot, config, failure):
    if os.environ.get("MONEYGRAPH_AI_API_TESTS") != "1":
        pytest.skip("Set MONEYGRAPH_AI_API_TESTS=1 with C's API on PYTHONPATH")
    from dataclasses import replace
    from fastapi import APIRouter, HTTPException
    from fastapi.testclient import TestClient
    from moneygraph.api import create_app

    directory = snapshot[0].directory
    app = create_app(directory)
    builder = SnapshotContextBuilder(directory)
    assert builder.run_id == app.state.query_service.run_id
    context = builder.for_client(snapshot[1]["collector"])
    cfg = replace(config, enabled=False) if failure == "disabled" else config
    ai = ExplanationService(cfg, transport=FakeTransport(context, error=AIUnavailable(failure)))
    router = APIRouter()

    @router.post("/api/v1/runs/{run_id}/nodes/{gid}/ai-explanation")
    def example_route(run_id: str, gid: str):
        if run_id != builder.run_id:
            raise HTTPException(404)
        try:
            return ai.explain_client(builder.for_client(gid))
        except AIUnavailable as error:
            raise HTTPException(503, {"code": error.code, "message": str(error)}) from None

    # C's catch-all /api and static mount must stay AFTER the new route.
    app.router.routes[0:0] = router.routes
    client = TestClient(app)
    root = f"/api/v1/runs/{builder.run_id}"
    path = root + f"/nodes/{context.client_id}"
    before = client.get(path)
    assert before.status_code == 200
    result = client.post(path + "/ai-explanation")
    assert result.status_code == 503
    assert result.json()["detail"]["message"] == "AI-объяснение недоступно"
    assert client.get(path).json() == before.json()
    assert client.get(path + "/transactions").status_code == 200
    assert client.get(root + "/graph").status_code == 200
    assert client.get(root + "/exports/nodes_roles.csv").content == (directory / "nodes_roles.csv").read_bytes()
    assert client.post("/api/v1/runs/other/nodes/1/ai-explanation").status_code == 404
