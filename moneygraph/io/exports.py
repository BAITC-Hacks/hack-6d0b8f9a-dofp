"""Serialize A's features and B's decisions, without recomputing graph metrics."""
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from numbers import Integral, Real
from pathlib import Path

import pandas as pd
import numpy as np

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
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist(), key)
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
        if value.tzinfo is not None or any((value.hour, value.minute, value.second, value.microsecond)):
            raise ValueError("Output reconciliation failed: dates must have calendar-day precision without timezone")
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


def cluster_records(scored: list[ScoredNode], stats: pd.DataFrame, graph) -> list[dict]:
    members = defaultdict(list)
    for node in scored:
        members[node.features.cluster_id].append(node)
    if stats.cluster_id.duplicated().any() or set(stats.cluster_id) != set(members):
        raise ValueError("Cluster statistics do not match assigned nodes")
    membership = {n.features.gid: n.features.cluster_id for n in scored}
    flows = {cid: {"internal": 0, "incoming": 0, "outgoing": 0} for cid in members}
    for src, dst, attrs in graph.edges(data=True):
        source, target = membership[src], membership[dst]
        amount = int(attrs["sum_minor"])
        if source == target:
            flows[source]["internal"] += amount
        else:
            flows[source]["outgoing"] += amount
            flows[target]["incoming"] += amount
    result = []
    for row in stats.sort_values("cluster_id").itertuples(index=False):
        group = members[row.cluster_id]
        if row.n_nodes != len(group) or row.n_seed != sum(n.features.is_seed for n in group):
            raise ValueError("Cluster membership counts disagree")
        flow = flows[row.cluster_id]
        if row.sum_minor_internal != flow["internal"]:
            raise ValueError("Cluster internal money disagrees with directed edges")
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
            purpose = "Недостаточно признаков для гипотезы о назначении сообщества"
        leaders = ", ".join(f"{n.features.gid} ({n.assignment.role}, {n.priority_score:.3f})" for n in top[:3])
        result.append({"cluster_id": int(row.cluster_id), "n_nodes": len(group),
                       "n_seed": int(row.n_seed), "sum_kzt_internal": kzt(int(row.sum_minor_internal)),
                       "top_gids": json.dumps([str(n.features.gid) for n in top]),
                       "hypothesis": (f"{purpose}. {composition}; seed={row.n_seed}; "
                                      f"граница={boundary}; внутренний оборот={kzt(int(row.sum_minor_internal))} KZT. "
                                      f"Межкластерный вход={kzt(flow['incoming'])} KZT; "
                                      f"выход={kzt(flow['outgoing'])} KZT. Лидеры: {leaders}. "
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
    groups = cluster_records(scored, clusters, graph)
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


def validate_outputs(directory: Path, *, data, features, graph, clusters,
                     scored: list[ScoredNode], top_limit: int, counts: dict) -> None:
    """Read every artifact back before publication, comparing to validated inputs.

    Checksums alone detect byte corruption, not a validly written wrong value.
    This boundary also catches dropped columns, rows, directions or explanations.
    """
    def require(condition, message):
        if not condition:
            raise ValueError(f"Output reconciliation failed: {message}")

    def same_json(actual, expected):
        # Python considers True == 1 and 1.0 == 1; the wire contract does not.
        return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)

    def check_csv(name, columns, expected):
        with (directory / name).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            require(tuple(reader.fieldnames or ()) == columns, f"{name} columns")
        require(rows == [{key: str(row[key]) for key in columns} for row in expected], name)

    node_rows = [n.csv_record() for n in scored]
    check_csv("nodes_roles.csv", NODE_COLUMNS, node_rows)
    check_csv("top_nodes.csv", TOP_COLUMNS, top_records(scored, limit=top_limit))
    check_csv("clusters.csv", CLUSTER_COLUMNS, cluster_records(scored, clusters, graph))
    for name, source in (("features", features), ("transactions", data.transactions)):
        restored = pd.read_parquet(directory / f"{name}.parquet")
        require(list(restored.columns) == list(source.columns), f"{name} columns")
        require(same_json(json_safe(restored.to_dict(orient="records")), json_safe(source.to_dict(orient="records"))),
                f"{name} records")
    graph_json = json.loads((directory / "graph.json").read_text(encoding="utf-8"))
    expected_edges = [{"src": int(r.src), "dst": int(r.dst), "sum_minor": int(r.sum_minor),
                       "n_tx": int(r.n_tx), "depth": int(r.depth)} for r in data.edges.itertuples(index=False)]
    require(graph_json["directed"] is True and graph_json["currency"] == "KZT" and graph_json["scale"] == 2,
            "graph units and direction")
    require(same_json(graph_json["edges"], json_safe(expected_edges)), "graph edges vs validated transactions/amounts/counts")
    by_gid = {row["gid"]: row for row in node_rows}
    expected_nodes = [{**r, **by_gid[r["gid"]]} for r in features.to_dict(orient="records")]
    require(same_json(graph_json["nodes"], json_safe(expected_nodes)), "graph nodes vs features/roles")
    explanations = [json.loads(line) for line in (directory / "explanations.jsonl").read_text(encoding="utf-8").splitlines()]
    require(same_json(explanations, json_safe([n.explanation_record() for n in scored])), "explanations")
    require(same_json(json.loads((directory / "quality.json").read_text(encoding="utf-8")), json_safe(data.quality)), "quality")
    require(counts == {"nodes": len(data.nodes), "edges": len(data.edges), "transactions": len(data.transactions),
                       "clusters": len(clusters), "top_nodes": min(top_limit, len(data.nodes))}, "manifest counts")
