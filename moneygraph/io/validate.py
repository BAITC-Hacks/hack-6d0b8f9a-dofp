"""Strict integrity checks; money is represented by integer tiyn (scale 2)."""
from decimal import Decimal, InvalidOperation
from numbers import Integral

import pandas as pd


class DataValidationError(ValueError):
    """The input cannot be used to produce a consistent graph."""


def _require(condition, message):
    if not condition:
        raise DataValidationError(message)


def _integers(series, label, minimum=-(2**63), maximum=2**63 - 1):
    _require(all(isinstance(v, Integral) and not isinstance(v, bool)
                 and minimum <= int(v) <= maximum for v in series),
             f"{label}: expected integers in [{minimum}, {maximum}]")


def to_minor(value):
    """Reject fractions of a tiyn instead of silently rounding them."""
    try:
        amount = Decimal(str(value))
        _require(amount.is_finite() and amount > 0, "sum_kzt must be finite and positive")
        minor = amount * 100
        _require(minor == minor.to_integral_value(), "sum_kzt has more than two decimal places")
        _require(minor <= 2**63 - 1, "sum_kzt exceeds supported integer range")
        return int(minor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"Invalid sum_kzt: {value!r}: {exc}") from exc


def validate_tables(nodes, edges, transactions, *, period_start, period_end,
                    max_depth=4, min_amount_minor=500_000):
    """Return normalized copies (nodes, edges, transactions, quality).

    Dates delimit the declared extraction window, never inferred from activity.
    Duplicate transaction rows are preserved. No input frame is modified.
    """
    start, end = pd.Timestamp(period_start), pd.Timestamp(period_end)
    _require(not pd.isna(start) and not pd.isna(end) and start <= end,
             "Invalid reporting period")
    _require(start.tzinfo is None and end.tzinfo is None
             and start == start.normalize() and end == end.normalize(),
             "Period must contain timezone-free dates")
    _integers([max_depth], "max_depth", 1)
    _integers([min_amount_minor], "min_amount_minor", 1)
    required = {
        "nodes": (nodes, ["gid", "depth", "is_seed"]),
        "edges": (edges, ["src", "dst", "sum_kzt", "n_tx", "depth"]),
        "transactions": (transactions, ["src", "dst", "date", "sum_kzt"]),
    }
    frames = {}
    for name, (frame, columns) in required.items():
        _require(set(columns) <= set(frame.columns), f"{name}: missing required columns")
        _require(not frame[columns].isna().any().any(), f"{name}: null required values")
        frames[name] = frame[columns].copy().reset_index(drop=True)
    n, e, tx = (frames[name] for name in required)
    _require(len(n) > 0, "nodes must not be empty")
    _integers(n.gid, "nodes.gid")
    _integers(n.depth, "nodes.depth", 0, max_depth)
    _require(pd.api.types.is_bool_dtype(n.is_seed), "is_seed must be boolean")
    _require(not n.gid.duplicated().any(), "Duplicate gid")
    _require((n.is_seed == (n.depth == 0)).all(), "is_seed must agree with depth=0")
    known = set(n.gid)
    for name, frame in [("edges", e), ("transactions", tx)]:
        for col in ["src", "dst"]:
            _integers(frame[col], f"{name}.{col}")
            _require(set(frame[col]) <= known, f"{name}: unknown {col}")
        # Python integers prevent overflow in aggregation before range checks.
        frame["sum_minor"] = pd.Series([to_minor(v) for v in frame.sum_kzt], dtype=object)
    _integers(e.n_tx, "edges.n_tx", 1)
    _integers(e.depth, "edges.depth", 1, max_depth)
    _require(not e.duplicated(["src", "dst"]).any(), "Duplicate directed edge")
    _require(all(v >= min_amount_minor for v in tx.sum_minor), "Transaction below sampling threshold")
    try:
        tx["date"] = pd.to_datetime(tx.date, errors="raise")
        _require(tx.date.dt.tz is None and (tx.date == tx.date.dt.normalize()).all(),
                 "Transactions must use date-only timestamps")
        _require(tx.date.between(start, end).all(), "Transaction outside reporting period")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DataValidationError(f"Invalid transaction dates: {exc}") from exc
    tx["tx_ref"] = range(len(tx))  # Reference within the source file, not an operation ID.
    observed = {}
    for row in tx.itertuples(index=False):
        key = (int(row.src), int(row.dst))
        amount, count = observed.get(key, (0, 0))
        observed[key] = (amount + row.sum_minor, count + 1)
    expected = {(int(r.src), int(r.dst)): (r.sum_minor, int(r.n_tx))
                for r in e.itertuples(index=False)}
    _require(observed.keys() == expected.keys(), "Transaction/edge pairs differ")
    _require(observed == expected, "Transaction/edge amounts or counts differ")
    total = sum(e.sum_minor, 0)
    _require(total <= 2**63 - 1, "Total money exceeds supported integer range")
    duplicate_count = int(tx.duplicated(["src", "dst", "date", "sum_minor"]).sum())
    quality = {
        "schema_version": "data-graph.v1", "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(), "max_depth": max_depth,
        "min_amount_minor": min_amount_minor, "n_nodes": len(n), "n_edges": len(e),
        "n_transactions": len(tx), "n_seed": int(n.is_seed.sum()),
        "total_minor": total, "duplicate_transaction_rows_preserved": duplicate_count,
        "self_loop_edges": int((e.src == e.dst).sum()),
        "reconciliation": {"pairs": True, "amounts_minor": True, "counts": True},
        "warnings": ["date_only", "sampling_threshold", "intrabank_only", "outgoing_sample"],
    }
    return (n.sort_values("gid").reset_index(drop=True),
            e.sort_values(["src", "dst"]).reset_index(drop=True), tx, quality)
