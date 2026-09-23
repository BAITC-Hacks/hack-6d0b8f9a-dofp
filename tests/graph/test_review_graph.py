import importlib.util
import os
from pathlib import Path

import pytest

from moneygraph.io.load import load_dataset
from moneygraph.analytics.graph import build_graph
from moneygraph.analytics.communities import detect_communities
from moneygraph.analytics.features import compute_features

spec = importlib.util.spec_from_file_location("role_a_review", Path(__file__).with_name("review_graph.py"))
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_ari_is_label_invariant_and_detects_crossing_groups():
    a = dict(enumerate([0, 0, 1, 1]))
    relabeled = dict(enumerate([9, 9, 5, 5]))
    crossed = dict(enumerate([0, 1, 0, 1]))
    assert review.adjusted_rand(a, relabeled) == 1
    assert review.adjusted_rand(a, crossed) == -.5
    assert review.adjusted_rand(a, dict.fromkeys(a, 0)) == 0
    assert review.adjusted_rand({}, {}) == 1
    assert review.adjusted_rand({1: 2}, {1: 3}) == 1
    with pytest.raises(ValueError, match="same node set"):
        review.adjusted_rand({1: 1}, {2: 1})


@pytest.mark.skipif(not os.environ.get("MONEYGRAPH_DATA_DIR"), reason="Local case data not supplied")
def test_real_stability_and_demo_witnesses():
    d = load_dataset(os.environ["MONEYGRAPH_DATA_DIR"],
                     period_start="2026-07-01", period_end="2026-07-31")
    g = build_graph(d.nodes, d.edges)
    report = review.stability_report(g)
    for row in report["results"]:
        assert sum(row["sizes_sorted"]) == len(g)
        assert sum(row["size_histogram"].values()) == row["n_clusters"]
        assert int(row["internal_minor"]) + int(row["intercluster_minor"]) == 36589001201
        assert 0 <= row["internal_turnover_share"] <= 1
        assert -.5 <= row["ari_vs_baseline_all"] <= 1
        if row["resolution"] == 1:
            assert row["ari_vs_baseline_all"] == row["ari_vs_baseline_largest_component"] == 1
            assert row["nodes_with_identical_group"] == len(g)
    assignment, _, _ = detect_communities(g)
    f, _ = compute_features(g, d.transactions, assignment, period_end="2026-07-31")
    scenarios = review.demo_scenarios(g, f)
    assert len(scenarios) == 5 and all(s["available"] for s in scenarios)
    by_kind = {s["scenario"]: s for s in scenarios}
    assert by_kind["isolated_seed"]["expected"]["isolated"]
    assert by_kind["depth_boundary"]["expected"]["out_tx"] == 0
    assert len(by_kind["converging_seeds"]["seed_paths"]) >= 2
    assert len(by_kind["collection"]["incoming_edges"]) >= 3
    assert len(by_kind["distribution"]["outgoing_edges"]) >= 3
    assert any(len(p) == 5 for p in by_kind["depth_boundary"]["seed_paths"])
    labels = dict(zip(assignment.gid, assignment.cluster_id))
    for scenario in scenarios:
        gid = int(scenario["gid"])
        assert gid in g
        for link in scenario["incoming_edges"]:
            assert int(link["dst"]) == gid and g.has_edge(int(link["src"]), gid)
        for link in scenario["outgoing_edges"]:
            assert int(link["src"]) == gid and g.has_edge(gid, int(link["dst"]))
        for path in scenario["seed_paths"]:
            route = list(map(int, path))
            assert route[-1] == gid and route[0] != gid and g.nodes[route[0]]["is_seed"]
            assert all(g.has_edge(a, b) for a, b in zip(route, route[1:]))
        for link in scenario["cross_cluster_edges"]:
            src, dst = int(link["src"]), int(link["dst"])
            assert g.has_edge(src, dst) and labels[src] != labels[dst]
    # No selection depends on the input DataFrame row ordering.
    assert scenarios == review.demo_scenarios(g, f.sample(frac=1, random_state=17))
