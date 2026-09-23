from decimal import Decimal
import hashlib

import pandas as pd
import pytest

from moneygraph.io.load import load_dataset
from moneygraph.io.validate import DataValidationError, to_minor, validate_tables


@pytest.fixture
def tables():
    nodes = pd.DataFrame({"gid": [1, 2, 3], "depth": [0, 1, 0],
                          "is_seed": [True, False, True]})
    edges = pd.DataFrame({"src": [1], "dst": [2], "sum_kzt": [10000.02],
                          "n_tx": [2], "depth": [1]})
    tx = pd.DataFrame({"src": [1, 1], "dst": [2, 2],
                       "date": ["2026-07-02"] * 2, "sum_kzt": [5000.01] * 2})
    return nodes, edges, tx


def validate(tables):
    return validate_tables(*tables, period_start="2026-07-01", period_end="2026-07-31")


def test_money_exact_and_duplicate_operations_preserved(tables):
    n, e, tx, quality = validate(tables)
    assert len(n) == 3 and len(tx) == 2
    assert tx.sum_minor.tolist() == [500001, 500001]
    assert e.sum_minor.tolist() == [1000002]
    assert tx.tx_ref.tolist() == [0, 1]
    assert quality["duplicate_transaction_rows_preserved"] == 1
    assert "sum_minor" not in tables[1]  # No mutation.
    assert to_minor(Decimal("1.23")) == 123


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 1.234,
                                   True, "invalid", "92233720368547758.08"])
def test_invalid_money_rejected(value):
    with pytest.raises(DataValidationError):
        to_minor(value)


@pytest.mark.parametrize("change", ["amount", "count", "unknown", "depth", "seed", "gid",
                                    "duplicate_edge", "outside", "null", "subthreshold"])
def test_inconsistent_tables_rejected(tables, change):
    n, e, tx = (t.copy() for t in tables)
    if change == "amount": e.loc[0, "sum_kzt"] += .01
    elif change == "count": e.loc[0, "n_tx"] = 1
    elif change == "unknown": tx.loc[0, "dst"] = 100
    elif change == "depth": n.loc[1, "depth"] = 5
    elif change == "seed": n.loc[1, "is_seed"] = True
    elif change == "gid": n.loc[1, "gid"] = 1
    elif change == "duplicate_edge": e = pd.concat([e, e], ignore_index=True)
    elif change == "outside": tx.loc[0, "date"] = "2026-08-01"
    elif change == "null": tx.loc[0, "sum_kzt"] = None
    elif change == "subthreshold": tx.loc[0, "sum_kzt"] = 4999
    with pytest.raises(DataValidationError):
        validate((n, e, tx))


def test_loader_hashes_exact_source_bytes_and_refs(tables, tmp_path):
    for name, frame in zip(["nodes", "edges", "transactions"], tables):
        frame.to_parquet(tmp_path / f"{name}.parquet", index=False)
    data = load_dataset(tmp_path, period_start="2026-07-01", period_end="2026-07-31")
    for filename, digest in data.quality["input_sha256"].items():
        assert digest == hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest()
    assert data.transactions.tx_ref.tolist() == [0, 1]


def test_all_isolated_dataset(tables):
    n, e, tx = tables
    _, e, tx, quality = validate((n, e.iloc[:0], tx.iloc[:0]))
    assert quality["total_minor"] == 0 and len(tx) == len(e) == 0


def test_integer_identifiers_are_not_silently_coerced(tables):
    n, e, tx = tables
    n["gid"] = n.gid.astype(float)
    with pytest.raises(DataValidationError):
        validate((n, e, tx))
