"""Deterministic role-neutral features for every validated node."""
from collections import deque
from numbers import Integral

import networkx as nx
import pandas as pd


def seed_reachability(graph, *, max_hops=4, max_examples=3):
    if any(not isinstance(v, Integral) or isinstance(v, bool) or v < 0
           for v in (max_hops, max_examples)):
        raise ValueError("Traversal limits must be nonnegative integers")
    counts, examples = {g: 0 for g in graph}, {g: [] for g in graph}
    for seed in sorted(g for g, a in graph.nodes(data=True) if a["is_seed"]):
        queue, visited = deque([(seed, [seed])]), {seed}
        while queue:
            current, path = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue
            for nxt in sorted(graph.successors(current)):
                if nxt in visited:
                    continue
                visited.add(nxt)
                route = path + [nxt]
                counts[nxt] += 1
                if len(examples[nxt]) < max_examples:
                    examples[nxt].append(route)
                queue.append((nxt, route))
    return counts, examples


def compute_features(graph, transactions, assignment, *, period_end,
                     max_hops=4, betweenness_k=128, seed=42):
    """Return (FeatureTable DataFrame, metric metadata).

    Nullable ratios and dates are intentionally retained, not filled with zero.
    Numeric fields are in tiyn. JSON serialization of IDs/money belongs to B/C.
    """
    if not isinstance(betweenness_k, Integral) or isinstance(betweenness_k, bool) or betweenness_k < 1:
        raise ValueError("betweenness_k must be a positive integer")
    if assignment.gid.duplicated().any() or set(assignment.gid) != set(graph):
        raise ValueError("Community assignment must cover every node exactly once")
    if assignment.cluster_id.isna().any():
        raise ValueError("Missing cluster_id")
    end = pd.Timestamp(period_end)
    if pd.isna(end) or end.tzinfo is not None or end != end.normalize():
        raise ValueError("period_end must be an explicit timezone-free date")
    clusters = dict(zip(assignment.gid, assignment.cluster_id))
    components = sorted(nx.weakly_connected_components(graph), key=min)
    component_ids = {g: cid for cid, group in enumerate(components) for g in group}
    k = min(betweenness_k, len(graph))
    centrality = nx.betweenness_centrality(graph, k=k, normalized=True, weight=None, seed=seed)
    pagerank = nx.pagerank(graph, weight="sum_minor")
    counts, paths = seed_reachability(graph, max_hops=max_hops)
    active_in = transactions.groupby("dst").date.nunique().to_dict()
    active_out = transactions.groupby("src").date.nunique().to_dict()
    last_in = transactions.groupby("dst").date.max().to_dict()
    last_out = transactions.groupby("src").date.max().to_dict()
    # A self-transfer counts as activity once on that calendar date.
    dates = pd.concat([transactions[["src", "date"]].rename(columns={"src": "gid"}),
                       transactions[["dst", "date"]].rename(columns={"dst": "gid"})])
    active = dates.groupby("gid").date.nunique().to_dict()
    rows = []
    for gid in sorted(graph):
        attrs = graph.nodes[gid]
        incoming = list(graph.in_edges(gid, data=True))
        outgoing = list(graph.out_edges(gid, data=True))
        predecessors = set(graph.predecessors(gid)) - {gid}
        successors = set(graph.successors(gid)) - {gid}
        in_minor = sum(d["sum_minor"] for _, _, d in incoming)
        out_minor = sum(d["sum_minor"] for _, _, d in outgoing)
        boundary = attrs["depth"] == graph.graph.get("max_depth", 4)
        isolated = not incoming and not outgoing
        recent = gid in last_in and last_in[gid] > end - pd.Timedelta(days=2)
        flags = ["date_only", "sampling_threshold"]
        for code, condition in [("depth_boundary", boundary),
                                ("seed_inflow_incomplete", attrs["is_seed"]),
                                ("isolated", isolated),
                                ("out_exceeds_observed_in", out_minor > in_minor),
                                ("month_end_window", recent)]:
            if condition:
                flags.append(code)
        if graph.has_edge(gid, gid):
            flags.append("self_transfer")
        rows.append({
            "gid": gid, "depth": attrs["depth"], "is_seed": attrs["is_seed"],
            "in_degree": len(predecessors), "out_degree": len(successors),
            "in_minor": in_minor, "out_minor": out_minor,
            "in_tx": sum(d["n_tx"] for _, _, d in incoming),
            "out_tx": sum(d["n_tx"] for _, _, d in outgoing),
            "active_in_days": active_in.get(gid, 0), "active_out_days": active_out.get(gid, 0),
            "active_days": active.get(gid, 0), "last_in_date": last_in.get(gid, pd.NaT),
            "last_out_date": last_out.get(gid, pd.NaT), "pagerank": pagerank[gid],
            "betweenness": centrality[gid], "component_id": component_ids[gid],
            "cluster_id": int(clusters[gid]), "reachable_seeds": counts[gid],
            "seed_paths": paths[gid],
            "other_neighbor_clusters": len({clusters[g] for g in predecessors | successors}
                                            - {clusters[gid]}),
            "observed_flow_ratio": out_minor / in_minor if in_minor else None,
            "flow_ratio_eligible": bool(in_minor and not attrs["is_seed"] and not boundary),
            "depth_boundary": boundary, "truncated_by_depth": boundary and not outgoing,
            "isolated": isolated, "month_end_window": bool(recent),
            "observation_flags": flags,
        })
    return pd.DataFrame(rows), {
        "betweenness": {"mode": "exact" if k == len(graph) else "sampled",
                        "k": k, "seed": seed, "weight": None, "normalized": True},
        "pagerank_weight": "sum_minor", "max_hops": max_hops, "max_path_examples": 3,
        "networkx_version": nx.__version__, "n_components": len(components),
        "n_isolates": len(list(nx.isolates(graph))),
    }
