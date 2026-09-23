from fastapi.testclient import TestClient
from moneygraph.api import create_app
from moneygraph.api.demo import demo_snapshot
from moneygraph.api.service import QueryService

HEADERS = {"Origin": "http://127.0.0.1", "X-Moneygraph-Client": "local-ui"}


def test_queue_reasons_and_quick_question_keep_existing_ranking_and_exports():
    payload = demo_snapshot()
    service = QueryService(payload)
    original = [node.gid for node in service.ranked]
    with TestClient(create_app(payload)) as client:
        rows = client.get('/api/v1/runs/demo-v1/nodes?limit=10').json()['data']['items']
        assert [node['gid'] for node in rows] == original[:10]
        assert all(node['queue_reason'] and node['priority_reason'] for node in rows)
        second = client.get('/api/v1/runs/demo-v1/nodes?limit=10&offset=10').json()['data']['items']
        assert [node['gid'] for node in second] == original[10:20]
        multi = client.get('/api/v1/runs/demo-v1/nodes?min_seed_reach=2').json()['data']['items']
        expected = [node.gid for node in service.ranked if node.metrics['reachable_seeds'] >= 2]
        assert [node['gid'] for node in multi] == expected
        assert client.get('/api/v1/runs/demo-v1/nodes?min_seed_reach=1').status_code == 422
        for role in ['consolidator','distributor']:
            filtered = client.get('/api/v1/runs/demo-v1/nodes?role='+role).json()['data']['items']
            assert [node['gid'] for node in filtered] == [node.gid for node in service.ranked if node.role == role]
        for name, data in service.exports.items():
            assert client.get('/api/v1/runs/demo-v1/exports/'+name).content == data


def test_review_persists_outside_snapshot_and_conflicts_do_not_overwrite(tmp_path):
    payload = demo_snapshot()
    gid = payload['nodes'][0]['gid']
    url = f'/api/v1/runs/demo-v1/nodes/{gid}/review'
    database = tmp_path / 'reviews.sqlite3'
    with TestClient(create_app(payload, review_path=database),base_url='http://127.0.0.1') as client:
        csv_before = client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content
        initial = client.get(url).json()['data']
        assert initial['version'] == 0 and initial['status'] == 'not_started'
        update = {'status':'in_progress','note':'Проверить входящие.\nСуммы сверены.','version':0}
        assert client.put(url,json=update).status_code == 403
        assert client.put(url,json=update,headers={**HEADERS,'Origin':'https://elsewhere.example'}).status_code == 403
        assert client.put(url,json=update,headers={**HEADERS,'Host':'elsewhere.example'}).status_code == 403
        first = client.put(url,json=update,headers=HEADERS)
        assert first.status_code == 200, first.text
        assert first.json()['data']['version'] == 1
        assert client.put(url,json={**update,'note':'stale edit'},headers=HEADERS).status_code == 409
        assert client.get(url).json()['data']['note'] == update['note']
        assert client.put(url,json={**update,'version':1,'status':'checked'},headers=HEADERS).status_code == 200
        assert client.put(url,json={**update,'status':'guilty'},headers=HEADERS).status_code == 422
        assert client.put(url,json={**update,'note':'x'*4001},headers=HEADERS).status_code == 422
        assert client.get(url.replace(gid,'999')).status_code == 404
        assert client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content == csv_before
    with TestClient(create_app(payload,review_path=database)) as client:
        saved = client.get(url).json()['data']
        assert saved['note'] == update['note'] and saved['status'] == 'checked' and saved['version'] == 2
        other = client.get(url.replace(gid,payload['nodes'][1]['gid'])).json()['data']
        assert other['version'] == 0
    payload['manifest']['run_id'] = 'other-run'
    with TestClient(create_app(payload,review_path=database)) as client:
        assert client.get(url.replace('demo-v1','other-run')).json()['data']['version'] == 0
