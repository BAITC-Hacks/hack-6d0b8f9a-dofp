from fastapi.testclient import TestClient
from moneygraph.api import create_app

IDS = [str(100000000000000001 + i) for i in range(6)]


def snapshot(run="path-test"):
    pairs = [(0,1),(1,2),(2,3),(3,4),(5,2),(2,1),(0,4)]
    edges = [{"src": IDS[a], "dst": IDS[b], "sum_minor": str(9007199254740993+i), "n_tx": i+1} for i,(a,b) in enumerate(pairs)]
    nodes = [{"gid": gid, "role": "peripheral", "role_score": 0.2, "priority_score": .1,
              "cluster_id": 1, "depth": [0,1,1,3,4,0][i], "is_seed": i in (0,5),
              "evidence": "Synthetic path test", "paths": []} for i,gid in enumerate(IDS)]
    nodes[4]["paths"] = [IDS[:5], [IDS[5],IDS[2],IDS[3],IDS[4]]]
    del nodes[5]["paths"]
    return {"manifest": {"run_id": run, "schema_version": "1.0"}, "nodes": nodes, "edges": edges,
            "clusters": [{"cluster_id": 1, "n_nodes": 6, "n_seed": 2,
                          "sum_minor_internal": str(sum(int(e["sum_minor"]) for e in edges)),
                          "top_gids": [IDS[4]], "hypothesis": "Synthetic"}]}


def endpoint(gid=IDS[4]):
    return f"/api/v1/runs/path-test/nodes/{gid}/path-view"


def test_complete_path_is_independent_of_regular_graph_limits_and_uses_exact_edges():
    with TestClient(create_app(snapshot())) as client:
        assert len(client.get('/api/v1/runs/path-test/graph?limit=2').json()['data']['nodes']) == 2
        result = client.get(endpoint()).json()
        assert result['run_id'] == 'path-test'
        view = result['data']
        assert view['state'] == 'found' and view['path'] == IDS[:5]
        assert view['total_nodes'] == 5 and view['total_edges'] == 4
        assert not view['truncated'] and view['hidden_nodes'] == view['hidden_edges'] == 0
        assert [n['step_index'] for n in view['nodes']] == list(range(5))
        assert all('role' in n and 'warnings' in n for n in view['nodes'])
        assert [(e['src'],e['dst']) for e in view['edges']] == list(zip(IDS[:4],IDS[1:5]))
        assert view['edges'][0]['sum_minor'] == '9007199254740993'
        assert view['edges'][0]['n_tx'] == 1
        assert 'depth_boundary' in view['warnings']
        second = client.get(endpoint()+'?path_index=1').json()['data']
        assert second['path'] == [IDS[5],IDS[2],IDS[3],IDS[4]]
        assert client.get(endpoint()+'?path_index=2').status_code == 422
        assert client.get(endpoint()+'?path_index=-1').status_code == 422
        assert client.get(endpoint('999')).status_code == 404
        assert client.get(endpoint('1.5')).status_code == 422
        assert client.get(endpoint().replace('path-test','other-run')).status_code == 404


def test_absent_examples_and_unavailable_data_keep_selected_node_visible():
    with TestClient(create_app(snapshot())) as client:
        empty = client.get(endpoint(IDS[0])).json()['data']
        missing = client.get(endpoint(IDS[5])).json()['data']
        assert empty['state'] == 'no_saved_paths' and empty['available_paths'] == 0
        assert missing['state'] == 'paths_unavailable' and missing['available_paths'] is None
        for view in (empty,missing):
            assert len(view['nodes']) == 1 and view['edges'] == []
            assert view['nodes'][0]['gid'] == view['focus_gid']


def test_invalid_saved_path_never_returns_partial_success():
    payload = snapshot()
    payload['nodes'][4]['paths'] = [[IDS[0],IDS[2],IDS[4]]]
    with TestClient(create_app(payload)) as client:
        result = client.get(endpoint())
        assert result.status_code == 409
        assert 'data' not in result.json()


def test_path_indexes_remain_owned_by_their_snapshot():
    app = create_app(snapshot())
    newer = snapshot('new-path-test')
    newer['nodes'][4]['paths'] = [newer['nodes'][4]['paths'][1]]
    from moneygraph.api.service import QueryService
    app.state.import_manager.publish(QueryService(newer))
    with TestClient(app) as client:
        assert client.get(endpoint()).json()['data']['path'] == IDS[:5]
        assert client.get(endpoint().replace('path-test','new-path-test')).json()['data']['path'][0] == IDS[5]
