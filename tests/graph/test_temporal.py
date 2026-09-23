from collections import defaultdict
from datetime import datetime
import json
import os

import pandas as pd
import pytest

from moneygraph.analytics.temporal import daily_activity
from moneygraph.io.load import load_dataset


CLIENT = "100000000000000001"  # Synthetic int64 identifier, above JS safe range.
NODE = {"gid": CLIENT, "depth": 1, "is_seed": False}
WINDOW = {"period_start": "2026-07-01", "period_end": "2026-07-03"}


def operation(src="2", dst=CLIENT, amount="500001", day="2026-07-01"):
    return {"src": src, "dst": dst, "sum_minor": amount, "date": day}


def test_exact_totals_repeats_self_transfer_zero_days_and_order():
    duplicate = operation()
    rows = [duplicate, dict(duplicate), operation(CLIENT, "3", "300002"),
            operation(CLIENT, CLIENT, "7", "2026-07-03"),
            operation("3", "4", "900000")]
    before = json.dumps(rows)
    result = daily_activity(NODE, rows, **WINDOW)
    assert result["gid"] == CLIENT and result["available"]
    assert result["totals"] == {"in_minor": "1000009", "out_minor": "300009",
        "net_minor": "700000", "in_tx": 3, "out_tx": 2, "self_tx": 1,
        "n_transactions": 4, "active_days": 2}
    assert [d["date"] for d in result["days"]] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert result["days"][1]["in_minor"] == result["days"][1]["out_minor"] == "0"
    assert result["days"][1]["n_transactions"] == 0
    assert "self_transfer" in result["warnings"]
    assert result == daily_activity(NODE, reversed(rows), **WINDOW)
    assert json.dumps(rows) == before
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_missing_transactions_are_not_zero_and_node_warnings_survive():
    seed = {**NODE, "depth": 0, "is_seed": True, "warnings": ["isolated"]}
    empty = daily_activity(seed, [], **WINDOW)
    missing = daily_activity(seed, None, **WINDOW)
    assert empty["available"] and len(empty["days"]) == 3
    assert empty["totals"]["n_transactions"] == 0
    assert {"seed_inflow_incomplete", "isolated", "no_observed_transactions"} <= set(empty["warnings"])
    assert not missing["available"] and missing["days"] == [] and missing["totals"] is None
    assert "transactions_not_loaded" in missing["warnings"]
    boundary = daily_activity({**NODE, "depth": 4}, [], **WINDOW)
    assert "depth_boundary" in boundary["warnings"]
    shallow = daily_activity({**NODE, "depth": 2}, [], max_depth=2, **WINDOW)
    assert "depth_boundary" in shallow["warnings"]


def test_large_integers_never_overflow_or_round_and_net_can_be_negative():
    amount = 2**63 - 1
    rows = [operation(CLIENT, "2", amount), operation(CLIENT, "2", amount)]
    result = daily_activity(NODE, rows, **WINDOW)
    assert result["totals"]["out_minor"] == str(2 * amount)
    assert result["totals"]["net_minor"] == str(-2 * amount)


def test_loader_dates_and_json_dates_produce_identical_results():
    frame = pd.DataFrame([operation()])
    frame["date"] = pd.to_datetime(frame.date)
    frame["src"] = frame.src.astype("int64")
    frame["dst"] = frame.dst.astype("int64")
    frame["sum_minor"] = frame.sum_minor.astype("int64")
    assert daily_activity(NODE, frame.to_dict("records"), **WINDOW) == daily_activity(NODE, [operation()], **WINDOW)
    single = daily_activity(NODE, [], period_start="2026-07-01", period_end="2026-07-01")
    assert len(single["days"]) == 1


@pytest.mark.parametrize("change", [
    {"sum_minor": 500001.0}, {"sum_minor": "1.5"}, {"sum_minor": 0},
    {"sum_minor": -1}, {"src": float(CLIENT)}, {"dst": True},
    {"date": "2026-06-30"}, {"date": "2026-07-04"},
    {"date": "2026-07-01T00:00:00Z"}, {"date": datetime(2026, 7, 1, 12)},
    {"date": None}, {"date": pd.NaT},
])
def test_invalid_client_rows_are_rejected(change):
    with pytest.raises(ValueError):
        daily_activity(NODE, [{**operation(), **change}], **WINDOW)


def test_reversed_period_is_rejected_even_when_transactions_unavailable():
    with pytest.raises(ValueError, match="period_start"):
        daily_activity(NODE, None, period_start="2026-07-03", period_end="2026-07-01")


@pytest.mark.skipif(not os.environ.get("MONEYGRAPH_DATA_DIR"), reason="Local case data not supplied")
def test_all_real_clients_reconcile_with_graph_and_full_transaction_history():
    data = load_dataset(os.environ["MONEYGRAPH_DATA_DIR"],
                        period_start="2026-07-01", period_end="2026-07-31")
    # Model C's complete per-client index, not its paginated HTTP response.
    indexed = defaultdict(list)
    for row in data.transactions.to_dict("records"):
        for gid in {row["src"], row["dst"]}:
            indexed[gid].append(row)
    expected = {int(g): [0, 0, 0, 0] for g in data.nodes.gid}
    for e in data.edges.itertuples():
        expected[e.src][1] += int(e.sum_minor)
        expected[e.src][3] += int(e.n_tx)
        expected[e.dst][0] += int(e.sum_minor)
        expected[e.dst][2] += int(e.n_tx)
    all_in = all_out = 0
    for node in data.nodes.to_dict("records"):
        rows = indexed[node["gid"]]
        result = daily_activity(node, rows, period_start="2026-07-01", period_end="2026-07-31")
        totals = result["totals"]
        assert len(result["days"]) == 31
        actual = [int(totals[k]) for k in ("in_minor", "out_minor", "in_tx", "out_tx")]
        assert actual == expected[node["gid"]]
        assert totals["n_transactions"] == len(rows)
        assert totals["active_days"] == len({r["date"] for r in rows})
        assert all(d["n_transactions"] == d["in_tx"] + d["out_tx"] - d["self_tx"] for d in result["days"])
        all_in += actual[0]
        all_out += actual[1]
    assert all_in == all_out == data.quality["total_minor"]
