from copy import deepcopy
import csv
import io
import json

from fastapi.testclient import TestClient
import pytest

from moneygraph.api import create_app
from moneygraph.api.demo import demo_snapshot
from moneygraph.api.service import QueryService


@pytest.fixture
def snapshot():
    return demo_snapshot()


@pytest.fixture
def client(snapshot):
    return TestClient(create_app(snapshot))


def test_empty_service_is_not_a_successful_empty_analysis():
    client = TestClient(create_app())
    assert client.get('/health').status_code == 503
    assert client.get('/api/v1/runs/current').json()['detail']['code'] == 'snapshot_not_loaded'


def test_missing_explicit_frontend_directory_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match='Frontend build not found'):
        create_app(demo_snapshot(), static_dir=tmp_path)


def test_demo_and_currency_are_explicit(client):
    body = client.get('/api/v1/runs/current').json()
    assert body['data']['demo'] is True
    assert body['run_id'] == 'demo-v1'
    assert body['data']['currency'] == 'KZT'
    assert body['data']['scale'] == 2
    assert 'synthetic_demo' in body['warnings']


def test_filter_pagination_and_rank_stay_global(client):
    first = client.get('/api/v1/runs/demo-v1/nodes?role=consolidator&limit=2').json()['data']
    second = client.get('/api/v1/runs/demo-v1/nodes?role=consolidator&limit=2&offset=2').json()['data']
    assert first['total'] == 3
    assert len(first['items']) == 2 and len(second['items']) == 1
    assert set(n['gid'] for n in first['items']).isdisjoint(n['gid'] for n in second['items'])
    assert all(n['role'] == 'consolidator' for n in first['items'] + second['items'])
    all_nodes = client.get('/api/v1/runs/demo-v1/nodes').json()['data']['items']
    ranks = {n['gid']: n['rank'] for n in all_nodes}
    assert all(n['rank'] == ranks[n['gid']] for n in first['items'])
    assert first['items'][0]['priority_score'] >= second['items'][0]['priority_score']


@pytest.mark.parametrize('path', ['nodes?limit=0', 'nodes?limit=501', 'nodes?offset=-1', 'nodes?role=criminal', 'graph?hops=3', 'graph?limit=501', 'nodes/104201/transactions?offset=-1'])
def test_unbounded_and_invalid_queries_are_rejected(client, path):
    assert client.get('/api/v1/runs/demo-v1/' + path).status_code == 422


def test_unknown_run_gid_and_export_are_clear(client):
    assert client.get('/api/v1/runs/old/nodes').status_code == 404
    assert client.get('/api/v1/runs/demo-v1/nodes/9999999').status_code == 404
    assert client.get('/api/v1/runs/demo-v1/graph?gid=9999999').status_code == 404
    assert client.get('/api/v1/runs/demo-v1/exports/manifest.json').status_code == 404
    assert client.get('/api/v1/does-not-exist').status_code == 404
    assert client.post('/api/v1/runs/demo-v1/nodes', json={}).status_code in (404, 405)


def test_neighborhood_keeps_directions_and_announces_truncation(client, snapshot):
    target = snapshot['nodes'][4]['gid']
    graph = client.get(f'/api/v1/runs/demo-v1/graph?gid={target}&hops=1&limit=2').json()['data']
    assert graph['nodes'][0]['gid'] == target
    assert graph['truncated'] is True
    assert graph['hidden_nodes'] == graph['total_nodes'] - 2
    assert graph['hidden_edges'] == graph['total_edges'] - len(graph['edges'])
    original_pairs = {(e['src'], e['dst']) for e in snapshot['edges']}
    assert all((e['src'], e['dst']) in original_pairs for e in graph['edges'])
    assert {n['gid'] for n in graph['nodes']} >= {e['src'] for e in graph['edges']} | {e['dst'] for e in graph['edges']}


def test_isolated_node_is_searchable_and_visible(snapshot):
    isolate = deepcopy(snapshot['nodes'][0])
    isolate.update(gid='9223372036854775807', cluster_id=4, evidence='Исходный узел без связей.', warnings=['isolated'])
    snapshot['nodes'].append(isolate)
    snapshot['clusters'].append({'cluster_id': 4, 'n_nodes': 1, 'n_seed': 1, 'sum_minor_internal': '0', 'top_gids': [isolate['gid']], 'hypothesis': 'Изолированный узел'})
    client = TestClient(create_app(snapshot))
    node = client.get(f"/api/v1/runs/demo-v1/nodes/{isolate['gid']}").json()['data']
    assert node['gid'] == '9223372036854775807'
    graph = client.get(f"/api/v1/runs/demo-v1/graph?gid={isolate['gid']}&hops=2").json()['data']
    assert len(graph['nodes']) == 1 and not graph['edges']


def test_transactions_preserve_repeated_operations(snapshot):
    duplicate = deepcopy(snapshot['transactions'][0])
    duplicate['tx_ref'] = 'same-fields-separate-operation'
    snapshot['transactions'].append(duplicate)
    client = TestClient(create_app(snapshot))
    tx = client.get(f"/api/v1/runs/demo-v1/nodes/{duplicate['src']}/transactions?limit=500").json()['data']
    identical = [row for row in tx['items'] if all(row[key] == duplicate[key] for key in ('src', 'dst', 'date', 'sum_minor'))]
    assert len(identical) == 2
    assert tx['date_precision'] == 'day'


def test_missing_transactions_are_not_reported_as_zero_activity(snapshot):
    del snapshot['transactions']
    client = TestClient(create_app(snapshot))
    response = client.get(f"/api/v1/runs/demo-v1/nodes/{snapshot['nodes'][0]['gid']}/transactions").json()
    assert response['data']['available'] is False
    assert 'transactions_not_loaded' in response['warnings']


def test_exports_match_api_and_do_not_depend_on_filters(client):
    client.get('/api/v1/runs/demo-v1/nodes?role=terminal')
    exported = client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv')
    assert exported.headers['x-run-id'] == 'demo-v1'
    assert 'attachment' in exported.headers['content-disposition']
    rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 24
    for row in rows:
        node = client.get(f"/api/v1/runs/demo-v1/nodes/{row['gid']}").json()['data']
        assert row['role'] == node['role']
        assert float(row['priority_score']) == node['priority_score']
    top = client.get('/api/v1/runs/demo-v1/exports/top_nodes.csv')
    assert len(list(csv.DictReader(io.StringIO(top.content.decode('utf-8-sig'))))) >= 20


def test_snapshot_is_frozen_after_start(snapshot):
    client = TestClient(create_app(snapshot))
    gid = snapshot['nodes'][0]['gid']
    snapshot['nodes'][0]['priority_score'] = 1
    assert client.get(f'/api/v1/runs/demo-v1/nodes/{gid}').json()['data']['priority_score'] != 1


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown_edge', 'invalid_score', 'float_gid', 'bad_cluster'])
def test_inconsistent_snapshot_fails_before_serving(snapshot, mutation):
    if mutation == 'duplicate': snapshot['nodes'].append(snapshot['nodes'][0])
    if mutation == 'unknown_edge': snapshot['edges'][0]['dst'] = '9999999'
    if mutation == 'invalid_score': snapshot['nodes'][0]['priority_score'] = float('nan')
    if mutation == 'float_gid': snapshot['nodes'][0]['gid'] = 104201.0
    if mutation == 'bad_cluster': snapshot['clusters'][0]['n_nodes'] = 500
    with pytest.raises(ValueError):
        create_app(snapshot)


def test_directory_adapter_and_immutable_source_exports(tmp_path, snapshot):
    fixture = QueryService(snapshot)
    (tmp_path / 'manifest.json').write_text(json.dumps(snapshot['manifest']), encoding='utf-8')
    for name, content in fixture.exports.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / 'graph.json').write_text(json.dumps({'nodes': snapshot['nodes'], 'edges': snapshot['edges']}), encoding='utf-8')
    client = TestClient(create_app(tmp_path))
    original = client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content
    (tmp_path / 'nodes_roles.csv').write_text('changed after API startup', encoding='utf-8')
    assert client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content == original
    assert client.get('/api/v1/runs/current').json()['data']['stats']['n_seed'] == 6


def test_partial_directory_is_rejected(tmp_path):
    (tmp_path / 'manifest.json').write_text('{"run_id":"unfinished"}', encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete snapshot'):
        create_app(tmp_path)


def test_optional_nan_metrics_are_null_not_an_http_500(snapshot):
    snapshot['nodes'][0]['metrics']['pass_through'] = float('nan')
    client = TestClient(create_app(snapshot))
    result = client.get(f"/api/v1/runs/demo-v1/nodes/{snapshot['nodes'][0]['gid']}")
    assert result.status_code == 200
    assert result.json()['data']['metrics']['pass_through'] is None


def test_money_adapter_tolerates_float_noise_but_not_fractional_tiyn():
    from moneygraph.api.models import minor_units
    assert minor_units(100.01000000000001) == '10001'
    with pytest.raises(ValueError):
        minor_units('100.015')


def test_malformed_date_fails_at_loading(snapshot):
    snapshot['transactions'][0]['date'] = '2026-99-99'
    with pytest.raises(ValueError):
        create_app(snapshot)


def test_actual_team_scoring_shape_is_adapted_without_recalculation(snapshot):
    original = snapshot['nodes'][2]
    team_record = {
        'features': {'gid': original['gid'], 'depth': original['depth'], 'is_seed': False, 'cluster_id': original['cluster_id'], 'in_degree': 7, 'out_degree': 2, 'in_minor': '125000000', 'out_minor': '50000000', 'seed_paths': [['104201', original['gid']]], 'observation_flags': ['sampling_threshold']},
        'assignment': {'role': original['role'], 'role_score': original['role_score'], 'limitations': ['date_only'], 'caps': {}, 'candidates': [{'role': 'consolidator', 'rule_id': 'consolidator.v1', 'accepted': True, 'support': .82}, {'role': 'transit', 'rule_id': 'transit.v1', 'accepted': True, 'support': .6}]},
        'priority_score': original['priority_score'], 'evidence': original['evidence'],
        'contributions': {'seed_reach': .35, 'role_support': .20, 'betweenness': .18, 'volume': .15}, 'factors': {}, 'policy': {}, 'rules_version': 'roles-v1.2',
    }
    snapshot['nodes'][2] = team_record
    client = TestClient(create_app(snapshot))
    node = client.get(f"/api/v1/runs/demo-v1/nodes/{original['gid']}").json()['data']
    assert node['metrics']['in_deg'] == 7
    assert node['metrics']['in_minor'] == '125000000'
    assert set(node['warnings']) >= {'sampling_threshold', 'date_only'}
    assert node['contributions'][0]['value'] == .35
    assert node['rule_id'] == 'consolidator.v1'
    assert node['paths'][0][-1] == original['gid']
    assert node['alternatives'][0]['role'] == 'transit'
    assert node['role_score'] == original['role_score']


def test_parquet_style_list_columns_can_be_adapted(snapshot):
    np = pytest.importorskip('numpy')
    snapshot['nodes'][0]['observation_flags'] = np.array(['seed_inflow_incomplete', 'date_only'])
    snapshot['nodes'][0]['seed_paths'] = np.array([np.array(['104201'])], dtype=object)
    del snapshot['nodes'][0]['paths']
    service = QueryService(snapshot)
    assert service.node('104201')['paths'] == [['104201']]
    assert 'date_only' in service.node('104201')['warnings']
