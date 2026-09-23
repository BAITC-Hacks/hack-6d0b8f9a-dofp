import json
from hashlib import sha256
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from moneygraph import ai
from moneygraph.api import create_app
from moneygraph.api.service import QueryService
from moneygraph.api.demo import demo_snapshot
from moneygraph.config import PipelineConfig
from moneygraph.pipeline import run_pipeline
from conftest import FakeTransport

HEADERS = {'X-Moneygraph-Client': 'local-ui', 'Origin': 'http://127.0.0.1'}


@pytest.fixture
def enable_ai(monkeypatch, config):
    monkeypatch.setenv('MONEYGRAPH_AI_UI_ENABLED', 'true')
    monkeypatch.setattr(ai.AIConfig, 'from_env', staticmethod(lambda: config))
    calls = []
    def generate(context):
        calls.append((context.snapshot_id, context.client_id))
        return ai.ExplanationService(config, transport=FakeTransport(context)).explain_client(context)
    monkeypatch.setattr(ai, 'explain_client', generate)
    return calls


def test_real_endpoint_is_local_scoped_cited_and_keeps_exports(snapshot, enable_ai):
    completed, gids = snapshot
    root = f'/api/v1/runs/{completed.run_id}'
    url = root + f'/nodes/{gids["collector"]}'
    with TestClient(create_app(completed), base_url='http://127.0.0.1') as client:
        before = client.get(root + '/exports/nodes_roles.csv').content
        status = client.get(url + '/ai-status')
        assert status.json()['data']['ready'] is True
        assert status.headers['cache-control'] == 'no-store'
        for headers in [{}, {**HEADERS, 'Origin': 'https://other.example'}, {**HEADERS, 'Host': 'other.example'}]:
            assert client.post(url + '/ai-explanation', headers=headers).status_code == 403
        assert not enable_ai
        assert client.post(url + '/ai-explanation', json={'prompt': 'change scores'}, headers=HEADERS).status_code == 422
        assert client.post(root + '/nodes/999/ai-explanation', headers=HEADERS).status_code == 404
        assert client.post(url.replace(completed.run_id, 'unknown') + '/ai-explanation', headers=HEADERS).status_code == 404
        response = client.post(url + '/ai-explanation', headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()['data']
        assert data['client_id'] == gids['collector']
        assert data['metadata']['snapshot_id'] == completed.run_id
        assert data['metadata']['human_review_required'] is True
        facts = {fact['id'] for fact in data['facts']}
        assert all(set(statement['fact_ids']) <= facts for statement in data['summary'])
        assert 'alias_map' not in data
        assert enable_ai == [(completed.run_id, gids['collector'])]
        assert client.get(root + '/exports/nodes_roles.csv').content == before


@pytest.mark.parametrize('code,expected', [('provider_unavailable',503), ('unsupported_claim',503), ('busy',429)])
def test_provider_failure_is_safe_and_does_not_break_card(snapshot, enable_ai, monkeypatch, code, expected):
    def fail(context):
        raise ai.AIUnavailable(code)
    monkeypatch.setattr(ai, 'explain_client', fail)
    completed, gids = snapshot
    url = f'/api/v1/runs/{completed.run_id}/nodes/{gids["collector"]}'
    with TestClient(create_app(completed), base_url='http://127.0.0.1') as client:
        before = client.get(url).json()
        response = client.post(url+'/ai-explanation', headers=HEADERS)
        assert response.status_code == expected
        assert response.json()['detail']['code'] == code
        assert response.headers['cache-control'] == 'no-store'
        assert client.get(url).json() == before
        assert client.get(url+'/transactions').status_code == 200


def test_disable_hide_and_non_native_snapshot_do_not_call_model(snapshot, enable_ai, monkeypatch, config):
    completed, gids = snapshot
    url = f'/api/v1/runs/{completed.run_id}/nodes/{gids["collector"]}'
    app = create_app(completed)
    monkeypatch.setattr(ai.AIConfig, 'from_env', staticmethod(lambda: replace(config, enabled=False)))
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get(url+'/ai-status').json()['data']['code'] == 'disabled'
        assert client.post(url+'/ai-explanation', headers=HEADERS).json()['detail']['code'] == 'disabled'
        monkeypatch.setenv('MONEYGRAPH_AI_UI_ENABLED', 'false')
        assert client.get('/api/v1/runs/current').json()['data']['features']['ai_assistant'] is False
        assert client.post(url+'/ai-explanation', headers=HEADERS).json()['detail']['code'] == 'hidden'
    monkeypatch.setenv('MONEYGRAPH_AI_UI_ENABLED', 'true')
    monkeypatch.setattr(ai.AIConfig, 'from_env', staticmethod(lambda: config))
    payload = demo_snapshot()
    with TestClient(create_app(payload), base_url='http://127.0.0.1') as client:
        url = f'/api/v1/runs/demo-v1/nodes/{payload["nodes"][0]["gid"]}'
        assert client.post(url+'/ai-explanation', headers=HEADERS).json()['detail']['code'] == 'invalid_context'
    assert not enable_ai


def test_imported_run_and_old_run_use_their_own_context(snapshot, tmp_path, enable_ai):
    completed, gids = snapshot
    other = run_pipeline(completed.directory.parents[2] / 'data', tmp_path, config=PipelineConfig(seed=43))
    app = create_app(completed)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        app.state.import_manager.publish(QueryService(other))
        assert client.get('/health').json()['run_id'] == other.run_id
        for run in [completed.run_id, other.run_id, completed.run_id]:
            result = client.post(f'/api/v1/runs/{run}/nodes/{gids["collector"]}/ai-explanation', headers=HEADERS)
            assert result.status_code == 200, result.text
            assert result.json()['data']['metadata']['snapshot_id'] == run
        assert [run for run, _ in enable_ai] == [completed.run_id, other.run_id, completed.run_id]


def test_snapshot_replacement_cannot_mix_ai_and_loaded_card(snapshot, tmp_path, enable_ai):
    import shutil
    completed, gids = snapshot
    target = tmp_path / 'snapshot'
    shutil.copytree(completed.directory, target)
    app = create_app(target)
    # Even a self-consistent replacement with new hashes must match the API's pinned bytes.
    artifact = target / 'quality.json'
    artifact.write_bytes(artifact.read_bytes() + b'\n')
    manifest = json.loads((target / 'manifest.json').read_bytes())
    manifest['artifacts']['quality.json'] = sha256(artifact.read_bytes()).hexdigest()
    (target / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        url = f'/api/v1/runs/{completed.run_id}/nodes/{gids["collector"]}'
        result = client.post(url+'/ai-explanation', headers=HEADERS)
        assert result.status_code == 503
        assert result.json()['detail']['code'] == 'context_changed'
        assert client.get(url).status_code == 200
    assert not enable_ai
