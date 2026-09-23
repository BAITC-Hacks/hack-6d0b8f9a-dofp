"""Local review utility for role A; does not replace the product pipeline.

python tests/graph/review_graph.py --data data --out out/graph-review
The demo file contains dataset identifiers. Keep generated files local.
"""
import argparse
from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneygraph.analytics.communities import detect_communities
from moneygraph.analytics.features import compute_features
from moneygraph.analytics.graph import build_graph
from moneygraph.io.load import load_dataset


def adjusted_rand(left, right):
    """Hubert-Arabie ARI on equal node sets; integer pair counts, no sklearn dependency."""
    if set(left) != set(right):
        raise ValueError("Partitions must cover the same node set")
    n = len(left)
    if n < 2:
        return 1.0
    pairs = lambda x: x * (x - 1) // 2
    cells = Counter((left[g], right[g]) for g in left)
    a = sum(pairs(v) for v in Counter(left.values()).values())
    b = sum(pairs(v) for v in Counter(right.values()).values())
    common = sum(pairs(v) for v in cells.values())
    total = pairs(n)
    denominator = (a + b) * total - 2 * a * b
    if denominator == 0:
        return 1.0  # Equivalent trivial partitions, all together or all singletons.
    return 2 * (common * total - a * b) / denominator


def summarize_partition(graph, assignment, stats, meta):
    sizes = stats.n_nodes
    buckets = {"1": int((sizes == 1).sum()),
               "2-5": int(sizes.between(2, 5).sum()),
               "6-20": int(sizes.between(6, 20).sum()),
               "21-100": int(sizes.between(21, 100).sum()),
               ">100": int((sizes > 100).sum())}
    return {"n_nodes": len(graph), "n_clusters": len(stats),
            "clusters_multiple_seeds": int((stats.n_seed > 1).sum()),
            "size_min": int(sizes.min()), "size_median": float(sizes.median()),
            "size_p90_linear": float(sizes.quantile(.9)), "size_max": int(sizes.max()),
            "size_histogram": buckets, "sizes_sorted": sorted(map(int, sizes)),
            "internal_minor": str(meta["internal_minor"]),
            "intercluster_minor": str(meta["intercluster_minor"]),
            "total_minor": str(meta["total_minor"]),
            "internal_turnover_share": (meta["internal_minor"] / meta["total_minor"]
                                        if meta["total_minor"] else None)}


def stability_report(graph, *, seed=42):
    """Fixed seed/resolutions; compare memberships, never numeric cluster IDs."""
    partitions, results = {}, []
    largest = max(nx.weakly_connected_components(graph), key=lambda c: (len(c), -min(c)))
    for resolution in (.8, 1., 1.2):
        assignment, stats, meta = detect_communities(graph, resolution=resolution, seed=seed)
        partitions[resolution] = dict(zip(map(int, assignment.gid), map(int, assignment.cluster_id)))
        result = summarize_partition(graph, assignment, stats, meta)
        result["resolution"] = resolution
        results.append(result)
    baseline = partitions[1.]
    for result in results:
        current = partitions[result["resolution"]]
        result["ari_vs_baseline_all"] = adjusted_rand(baseline, current)
        result["ari_vs_baseline_largest_component"] = adjusted_rand(
            {g: baseline[g] for g in largest}, {g: current[g] for g in largest})
        # Fraction of nodes whose entire group (not label) is unchanged.
        groups_a, groups_b = {}, {}
        for gid in sorted(graph):
            groups_a.setdefault(baseline[gid], set()).add(gid)
            groups_b.setdefault(current[gid], set()).add(gid)
        unchanged = sum(groups_a[baseline[g]] == groups_b[current[g]] for g in graph)
        result["nodes_with_identical_group"] = unchanged
    return {"baseline_resolution": 1.0, "seed": seed,
            "largest_component_nodes": len(largest), "results": results,
            "note": "Sensitivity to resolution at fixed seed; not accuracy or random-seed robustness."}


def demo_scenarios(graph, features):
    """Select repeatable witnesses from actual graph; no hardcoded case gid."""
    rows = features.to_dict(orient="records")
    mapping = {int(r["gid"]): r for r in rows}
    used = set()
    scenarios = []

    def add(name, candidates, order, explanation, instructions):
        candidates = sorted(candidates, key=order)
        if not candidates:
            scenarios.append({"scenario": name, "available": False})
            return
        r = next((r for r in candidates if r["gid"] not in used), candidates[0])
        gid = int(r["gid"])
        used.add(gid)
        routes = r["seed_paths"]
        for path in routes:
            if (not path or path[-1] != gid or path[0] == gid
                    or not graph.nodes[path[0]]["is_seed"] or len(path) > 5
                    or not all(graph.has_edge(a, b) for a, b in zip(path, path[1:]))):
                raise ValueError("Invalid seed-path witness")
        # One oriented witness per distinct external community, max three.
        cross = []
        witnessed = set()
        links = sorted(set(graph.in_edges(gid)) | set(graph.out_edges(gid)))
        for src, dst in links:
            other = dst if src == gid else src
            cid = mapping[other]["cluster_id"]
            if cid != r["cluster_id"] and cid not in witnessed:
                cross.append({"src": str(src), "dst": str(dst), "other_cluster_id": int(cid)})
                witnessed.add(cid)
            if len(cross) == 3:
                break
        scenarios.append({"scenario": name, "available": True, "gid": str(gid),
                          "explanation": explanation, "steps": instructions,
                          "expected": {key: r[key] for key in
                                       ("depth", "is_seed", "in_degree", "out_degree", "in_tx", "out_tx",
                                        "reachable_seeds", "other_neighbor_clusters", "cluster_id",
                                        "isolated", "depth_boundary")},
                          "observation_flags": r["observation_flags"],
                          "incoming_edges": [{"src": str(a), "dst": str(b)}
                                             for a, b in sorted(graph.in_edges(gid)) if a != b][:5],
                          "outgoing_edges": [{"src": str(a), "dst": str(b)}
                                             for a, b in sorted(graph.out_edges(gid)) if a != b][:5],
                          "seed_paths": [[str(g) for g in path] for path in routes],
                          "cross_cluster_edges": cross})

    add("isolated_seed", [r for r in rows if r["is_seed"] and r["isolated"]],
        lambda r: r["gid"], "Исходный клиент сохранён, хотя переводов в выгрузке нет.",
        ["Найти gid", "Проверить нулевые потоки и пустую окрестность", "Показать флаг isolated"])
    add("depth_boundary", [r for r in rows if r["depth_boundary"] and r["out_tx"] == 0
                           and any(len(p) == 5 for p in r["seed_paths"])],
        lambda r: (-r["in_degree"], -r["in_minor"], r["gid"]),
        "Нет видимых исходящих: обход закончился; удержание денег не установлено.",
        ["Найти gid", "Показать входящие стрелки и depth=4", "Проверить предупреждение о границе"])
    add("converging_seeds", [r for r in rows if r["reachable_seeds"] >= 2 and len(r["seed_paths"]) >= 2],
        lambda r: (-r["reachable_seeds"], -r["in_degree"], r["gid"]),
        "Несколько исходных ветвей структурно достигают клиента; это не трассировка тех же денег.",
        ["Открыть карточку", "Сопоставить reachable_seeds и примеры путей", "Показать два разных seed"])
    add("collection", [r for r in rows if r["in_degree"] >= 3
                       and r["in_degree"] >= 2 * max(1, r["out_degree"])
                       and not r["depth_boundary"] and not r["is_seed"]],
        lambda r: (-r["in_degree"], -r["in_minor"], r["gid"]),
        "Много плательщиков сходятся к узлу: структурный пример сбора, итоговую роль назначает B.",
        ["Найти gid", "Показать входящие стрелки", "Сравнить число плательщиков и получателей"])
    add("distribution", [r for r in rows if r["out_degree"] >= 3
                         and r["out_degree"] >= 2 * max(1, r["in_degree"])
                         and not r["depth_boundary"] and not r["is_seed"]],
        lambda r: (-r["out_degree"], -r["out_minor"], r["gid"]),
        "Узел переводит многим получателям: структурный пример распределения, итоговую роль назначает B.",
        ["Найти gid", "Показать исходящие стрелки", "Отличить число получателей от числа операций"])
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--period-start", default="2026-07-01")
    parser.add_argument("--period-end", default="2026-07-31")
    args = parser.parse_args()
    data = load_dataset(args.data, period_start=args.period_start, period_end=args.period_end)
    graph = build_graph(data.nodes, data.edges)
    report = stability_report(graph)
    assignment, _, _ = detect_communities(graph, resolution=1., seed=42)
    features, _ = compute_features(graph, data.transactions, assignment, period_end=args.period_end)
    provenance = {"input_sha256": data.quality["input_sha256"],
                  "period_start": args.period_start, "period_end": args.period_end,
                  "max_depth": 4, "max_hops": 4, "min_amount_minor": 500000,
                  "resolution": 1., "seed": 42, "betweenness_k": 128,
                  "python": platform.python_version(),
                  "dependencies": {name: version(name) for name in
                                   ("pandas", "pyarrow", "networkx", "numpy", "scipy")},
                  "source_sha256": {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest()
                                    for p in [Path(__file__), *sorted((ROOT / "moneygraph/analytics").glob("*.py")),
                                              *sorted((ROOT / "moneygraph/io").glob("*.py"))]}}
    report["provenance"] = provenance
    demo = {"provenance": provenance, "scenarios": demo_scenarios(graph, features)}
    args.out.mkdir(parents=True, exist_ok=True)
    for filename, payload in [("stability.json", report), ("demo-scenarios.local.json", demo)]:
        destination = args.out / filename
        # Refuse accidental overwrites of a previous review result.
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        print(destination)


if __name__ == "__main__":
    main()
