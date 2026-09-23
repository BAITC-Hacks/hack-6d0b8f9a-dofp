"""Native pipeline snapshot contract and corruption checks."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moneygraph.api import create_app
from moneygraph.api.demo import demo_snapshot
from moneygraph.api.service import QueryService, load_directory
from moneygraph.api.snapshot import ARTIFACTS, SCHEMA, read_snapshot


def test_verified_bytes_are_pinned_and_corruption_is_rejected(tmp_path):
    identity = {'config': {'seed': 42}}
    canonical = (json.dumps(identity, sort_keys=True, separators=(',', ':')) + '\n').encode()
    manifest = {'schema_version': SCHEMA, 'identity': identity,
                'run_id': sha256(canonical).hexdigest(), 'artifacts': {}}
    for name in ARTIFACTS:
        payload = name.encode()
        (tmp_path / name).write_bytes(payload)
        manifest['artifacts'][name] = sha256(payload).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    _, pinned = read_snapshot(tmp_path)
    (tmp_path / 'graph.json').write_bytes(b'changed')
    assert pinned['graph.json'] == b'graph.json'
    with pytest.raises(ValueError, match='checksum'):
        read_snapshot(tmp_path)
    manifest['run_id'] = '0' * 64
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='identity'):
        read_snapshot(tmp_path)


@pytest.mark.parametrize('field,value', [('n_nodes', 1.5), ('n_seed', True), ('cluster_id', 0.0), ('sum_minor_internal', -1)])
def test_cluster_values_are_not_silently_coerced(field, value):
    payload = demo_snapshot()
    payload['clusters'][0][field] = value
    with pytest.raises(ValueError):
        QueryService(payload)


@pytest.mark.parametrize('field,value', [('date', '2026-07-01T12:00:00'), ('date', '20260701'), ('sum_minor', '0')])
def test_transaction_values_are_not_silently_coerced(field, value):
    payload = demo_snapshot()
    payload['transactions'][0][field] = value
    with pytest.raises(ValueError):
        QueryService(payload)


@pytest.mark.skipif(not os.environ.get('MONEYGRAPH_SNAPSHOT_DIR'), reason='Set MONEYGRAPH_SNAPSHOT_DIR to a real pipeline snapshot')
def test_native_pipeline_snapshot_exports_and_all_nodes():
    directory = Path(os.environ['MONEYGRAPH_SNAPSHOT_DIR'])
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    @dataclass(frozen=True)
    class Locator:
        directory: Path
        run_id: str
    app = create_app(Locator(directory, manifest['run_id']))
    client = TestClient(app)
    summary = client.get('/api/v1/runs/current').json()['data']
    assert summary['demo'] is False
    assert summary['stats']['n_nodes'] == manifest['counts']['nodes']
    assert summary['period']['end'] == manifest['identity']['config']['period_end']
    base = '/api/v1/runs/' + manifest['run_id']
    for gid in app.state.query_service.nodes:
        assert client.get(base + '/nodes/' + gid).status_code == 200
    for name in ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv'):
        assert client.get(base + '/exports/' + name).content == (directory / name).read_bytes()
    for node in app.state.query_service.ranked[:3]:
        assert client.get(base + '/graph', params={'gid': node.gid, 'hops': 2}).status_code == 200
        assert client.get(base + '/nodes/' + node.gid + '/transactions').status_code == 200
    with pytest.raises(ValueError, match='locator'):
        create_app(Locator(directory, 'wrong-run'))
    payload = load_directory(directory)
    payload['transactions'].pop()
    with pytest.raises(ValueError, match='transactions do not reconcile'):
        QueryService(payload)
    payload = load_directory(directory)
    payload['clusters'][0]['sum_kzt_internal'] = '0.01'
    with pytest.raises(ValueError, match='cluster amounts'):
        QueryService(payload)
