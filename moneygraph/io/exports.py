"""Serialize A's features and B's decisions, without recomputing graph metrics."""
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

from moneygraph.analytics.ranking import ScoredNode, top_records

NODE_COLUMNS = ("gid", "role", "role_score", "cluster_id", "priority_score", "evidence")
CLUSTER_COLUMNS = ("cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis")
TOP_COLUMNS = ("rank", "gid", "role", "priority_score", "why")


def json_safe(value, key=""):
    """JSON boundary: exact IDs/money, ISO dates, and null for missing metrics."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {k: json_safe(v, k) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        child_key = "gid" if key in ("seed_paths", "top_gids", "gid") else ""
        return [json_safe(v, child_key) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return str(value) if key in ("gid", "src", "dst") or "minor" in key else int(value)
    if isinstance(value, Real):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Infinite metric cannot be serialized")
        return float(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True,
                               allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def kzt(minor: int) -> str:
    """No floating point conversion, including for sums above 2**53."""
    return f"{minor // 100}.{minor % 100:02d}"


def cluster_records(scored: list[ScoredNode], stats: pd.DataFrame) -> list[dict]:
    members = defaultdict(list)
    for node in scored:
        members[node.features.cluster_id].append(node)
    if stats.cluster_id.duplicated().any() or set(stats.cluster_id) != set(members):
        raise ValueError("Cluster statistics do not match assigned nodes")
    result = []
    for row in stats.sort_values("cluster_id").itertuples(index=False):
        group = members[row.cluster_id]
        if row.n_nodes != len(group) or row.n_seed != sum(n.features.is_seed for n in group):
            raise ValueError("Cluster membership counts disagree")
        counts = Counter(n.assignment.role for n in group)
        composition = ", ".join(f"{role}={count}" for role, count in sorted(counts.items()))
        boundary = sum(n.features.depth_boundary for n in group)
        top = sorted(group, key=lambda n: (-n.priority_score, n.features.gid))[:5]
        if all(n.features.isolated for n in group):
            purpose = "Нет наблюдаемых переводов; назначение неизвестно"
        elif counts["consolidator"] and counts["distributor"]:
            purpose = "Гипотеза: сбор и распределение средств"
        elif counts["consolidator"]:
            purpose = "Гипотеза: сбор средств"
        elif counts["distributor"]:
            purpose = "Гипотеза: распределение средств"
        elif counts["transit"]:
            purpose = "Гипотеза: передача средств между участниками"
        else:
            purpose = "Назначение сообщества не установлено"
        result.append({"cluster_id": int(row.cluster_id), "n_nodes": len(group),
                       "n_seed": int(row.n_seed), "sum_kzt_internal": kzt(int(row.sum_minor_internal)),
                       "top_gids": json.dumps([str(n.features.gid) for n in top]),
                       "hypothesis": (f"{purpose}. {composition}; seed={row.n_seed}; "
                                      f"граница={boundary}; внутренний оборот={kzt(int(row.sum_minor_internal))} KZT. "
                                      "Структурное сообщество, не доказательство связи вне переводов.")})
    return result


def write_csv(path: Path, columns, records) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def write_outputs(directory: Path, *, data, features, graph, clusters,
                  scored: list[ScoredNode], top_limit: int) -> dict:
    expected = set(data.nodes.gid)
    if {n.features.gid for n in scored} != expected or len(scored) != len(expected):
        raise ValueError("Scoring must cover every input node exactly once")
    if set(features.gid) != expected or len(features) != len(expected):
        raise ValueError("Feature table must cover every input node exactly once")
    roles = [n.csv_record() for n in scored]
    for row in roles:
        if not row["evidence"] or len(row["evidence"]) > 200:
            raise ValueError("Evidence must contain 1..200 characters")
        for key in ("role_score", "priority_score"):
            if not math.isfinite(row[key]) or not 0 <= row[key] <= 1:
                raise ValueError(f"Invalid {key}")
    groups = cluster_records(scored, clusters)
    top = top_records(scored, limit=top_limit)
    write_csv(directory / "nodes_roles.csv", NODE_COLUMNS, roles)
    write_csv(directory / "clusters.csv", CLUSTER_COLUMNS, groups)
    write_csv(directory / "top_nodes.csv", TOP_COLUMNS, top)
    features.to_parquet(directory / "features.parquet", index=False)
    # A's tx_ref identifies an original row; keep all duplicate transactions.
    data.transactions.to_parquet(directory / "transactions.parquet", index=False)
    with (directory / "explanations.jsonl").open("w", encoding="utf-8") as stream:
        for node in scored:
            stream.write(json.dumps(node.explanation_record(), ensure_ascii=False,
                                    sort_keys=True, allow_nan=False) + "\n")
    by_gid = {row["gid"]: row for row in roles}
    nodes = [{**row, **by_gid[row["gid"]]} for row in features.to_dict(orient="records")]
    edges = [{"src": src, "dst": dst, **attrs}
             for src, dst, attrs in sorted(graph.edges(data=True))]
    write_json(directory / "graph.json", {"directed": True, "currency": "KZT", "scale": 2,
                                          "nodes": nodes, "edges": edges})
    write_json(directory / "quality.json", data.quality)
    return {"nodes": len(roles), "clusters": len(groups), "top_nodes": len(top),
            "edges": len(edges), "transactions": len(data.transactions)}
