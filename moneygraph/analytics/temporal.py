"""Exact daily activity from a known client's complete observed transactions.

Accepts normalized loader records or API transaction records, without depending
on the API. It does not infer transaction ordering, balances or suspiciousness.
"""
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from numbers import Integral
import re


def _integer(value, name):
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        return int(value)
    raise ValueError(f"{name} must be an integer or decimal integer string")


def _gid(value):
    gid = _integer(value, "gid")
    if not -(2**63) <= gid < 2**63:
        raise ValueError("gid must fit int64")
    return str(gid)


def _day(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None or any((value.hour, value.minute, value.second,
                                          value.microsecond, getattr(value, "nanosecond", 0))):
            raise ValueError("Dates must be timezone-free and date-only")
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            result = date.fromisoformat(value)
            if result.isoformat() == value:
                return result
        except ValueError:
            pass
    raise ValueError("Dates must use YYYY-MM-DD or a date-only datetime")


def daily_activity(node: Mapping, transactions: Iterable[Mapping] | None, *,
                   period_start, period_end, max_depth=4) -> dict:
    """Return JSON-safe daily sums for one EXISTING node, inclusive of zero days.

    The caller must resolve node in the same validated snapshot as transactions.
    Pass all its transactions (or the complete ledger), never a paginated page.
    None means unavailable; an empty iterable means observed zero operations.
    Out-of-period client rows raise rather than silently changing the window.
    Repeated rows remain operations. Self-transfers contribute to both in/out,
    but n_transactions counts each input row once. Money uses strings of tiyn.
    """
    gid = _gid(node["gid"])
    max_depth = _integer(max_depth, "max_depth")
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    start, end = _day(period_start), _day(period_end)
    if start > end:
        raise ValueError("period_start must not exceed period_end")
    warnings = {"date_only", "sampling_threshold", "intrabank_only", "outgoing_sample"}
    warnings.update(node.get("observation_flags", []))
    warnings.update(node.get("warnings", []))
    if node.get("is_seed", False):
        warnings.add("seed_inflow_incomplete")
    if node.get("depth") == max_depth:
        warnings.add("depth_boundary")
    result = {"schema_version": "daily-activity.v1", "gid": gid,
              "period": {"start": start.isoformat(), "end": end.isoformat()},
              "currency": "KZT", "scale": 2, "date_precision": "day",
              "available": transactions is not None, "days": [], "totals": None,
              "warnings": []}
    if transactions is None:
        warnings.add("transactions_not_loaded")
        result["warnings"] = sorted(warnings)
        return result

    keys = ("in_minor", "out_minor", "in_tx", "out_tx", "self_tx", "n_transactions")
    daily = {}
    for offset in range((end - start).days + 1):
        daily[(start + timedelta(days=offset)).isoformat()] = dict.fromkeys(keys, 0)
    for tx in transactions:
        src, dst = _gid(tx["src"]), _gid(tx["dst"])
        if gid not in (src, dst):
            continue
        day = _day(tx["date"])
        if not start <= day <= end:
            raise ValueError("Client transaction outside reporting period")
        amount = _integer(tx["sum_minor"], "sum_minor")
        if amount <= 0:
            raise ValueError("sum_minor must be positive")
        row = daily[day.isoformat()]
        row["n_transactions"] += 1
        if dst == gid:
            row["in_minor"] += amount
            row["in_tx"] += 1
        if src == gid:
            row["out_minor"] += amount
            row["out_tx"] += 1
        if src == dst:
            row["self_tx"] += 1

    def public(row):
        return {**row, "in_minor": str(row["in_minor"]),
                "out_minor": str(row["out_minor"]),
                "net_minor": str(row["in_minor"] - row["out_minor"])}

    totals = {key: sum(row[key] for row in daily.values()) for key in keys}
    totals["active_days"] = sum(row["n_transactions"] > 0 for row in daily.values())
    if totals["self_tx"]:
        warnings.add("self_transfer")
    if not totals["n_transactions"]:
        warnings.add("no_observed_transactions")
    result["days"] = [{"date": day, **public(row)} for day, row in daily.items()]
    result["totals"] = public(totals)
    result["warnings"] = sorted(warnings)
    return result
