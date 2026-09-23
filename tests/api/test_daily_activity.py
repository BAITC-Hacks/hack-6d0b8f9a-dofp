from fastapi.testclient import TestClient
import pytest

from moneygraph.api import create_app

CLIENT = "100000000000000001"
ISOLATED = "100000000000000003"


def snapshot():
    nodes = [{"gid": gid, "role": "peripheral", "role_score": 0.2,
              "priority_score": 0.1, "cluster_id": 1, "evidence": "Synthetic test",
              "depth": 0 if gid == CLIENT else 1, "is_seed": gid == CLIENT}
             for gid in [CLIENT, "2", ISOLATED]]
    tx = [{"src": "2", "dst": CLIENT, "sum_minor": "500001", "date": "2026-07-01"} for _ in range(13)]
    tx.append({"src": CLIENT, "dst": CLIENT, "sum_minor": "7", "date": "2026-07-03"})
    return {"manifest": {"run_id": "daily-test", "schema_version": "1.0",
            "period": {"start": "2026-07-01", "end": "2026-07-03"}},
            "nodes": nodes, "transactions": tx,
            "edges": [{"src": "2", "dst": CLIENT, "sum_minor": "6500013", "n_tx": 13},
                      {"src": CLIENT, "dst": CLIENT, "sum_minor": "7", "n_tx": 1}],
            "clusters": [{"cluster_id": 1, "n_nodes": 3, "n_seed": 1,
                          "sum_minor_internal": "6500020", "top_gids": [CLIENT], "hypothesis": "Synthetic test"}]}


def url(gid=CLIENT):
    return f"/api/v1/runs/daily-test/nodes/{gid}/daily-activity"


def test_daily_api_uses_complete_history_exact_money_and_snapshot_period():
    with TestClient(create_app(snapshot())) as client:
        page = client.get(f"/api/v1/runs/daily-test/nodes/{CLIENT}/transactions?limit=10").json()["data"]
        assert len(page["items"]) == 10 and page["total"] == 14
        response = client.get(url())
        assert response.status_code == 200, response.text
        assert response.json()["run_id"] == "daily-test"
        data = response.json()["data"]
        assert data["gid"] == CLIENT and data["available"]
        assert data["totals"]["in_minor"] == "6500020"
        assert data["totals"]["out_minor"] == "7"
        assert data["totals"]["n_transactions"] == 14
        assert data["totals"]["self_tx"] == 1
        assert len(data["days"]) == 3
        assert data["days"][1]["n_transactions"] == 0
        assert data["days"][2]["net_minor"] == "0"
        assert "seed_inflow_incomplete" in data["warnings"]
        zero = client.get(url(ISOLATED)).json()["data"]
        assert zero["available"] and zero["totals"]["n_transactions"] == 0
        assert len(zero["days"]) == 3
        assert client.get(url("999")).status_code == 404
        assert client.get(url("1.5")).status_code == 422
        assert client.get(url().replace("daily-test", "unknown-run")).status_code == 404


def test_daily_api_distinguishes_unavailable_history_from_zero():
    payload = snapshot()
    del payload["transactions"]
    with TestClient(create_app(payload)) as client:
        data = client.get(url()).json()["data"]
        assert not data["available"] and data["totals"] is None and data["days"] == []


@pytest.mark.parametrize("period", [None, {"start": "2026-07-03", "end": "2026-07-01"},
                                      {"start": "1900-01-01", "end": "2026-07-31"}])
def test_daily_api_rejects_missing_invalid_or_unbounded_period(period):
    payload = snapshot()
    payload["manifest"]["period"] = period
    with TestClient(create_app(payload)) as client:
        assert client.get(url()).status_code == 422
