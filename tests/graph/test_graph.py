import math

import networkx as nx
import pandas as pd
from pandas.testing import assert_frame_equal

from moneygraph.io.validate import validate_tables
from moneygraph.analytics.graph import build_graph, community_projection
from moneygraph.analytics.communities import detect_communities
from moneygraph.analytics.features import compute_features, seed_reachability


def fixture(shuffle=False):
    nodes = pd.DataFrame({"gid": [1, 2, 3, 4, 5, 6, 7, 99],
                          "depth": [0, 0, 1, 2, 3, 4, 4, 0],
                          "is_seed": [True, True, False, False, False, False, False, True]})
    pairs = [(1, 3), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (4, 3)]
    tx = pd.DataFrame([{"src": s, "dst": d, "sum_kzt": 5000., "date": "2026-07-31"}
                       for s, d in pairs])
    edges = tx[["src", "dst", "sum_kzt"]].assign(n_tx=1, depth=1)
    if shuffle:
        nodes, edges, tx = (t.sample(frac=1, random_state=7) for t in [nodes, edges, tx])
    return validate_tables(nodes, edges, tx, period_start="2026-07-01", period_end="2026-07-31")


def compute(shuffle=False):
    n, e, tx, quality = fixture(shuffle)
    graph = build_graph(n, e)
    a, stats, metadata = detect_communities(graph)
    f, fm = compute_features(graph, tx, a, period_end=quality["period_end"])
    return graph, a, stats, metadata, f, fm


def test_isolated_nodes_and_seed_bfs_depth_limit():
    g, _, _, _, f, _ = compute()
    counts, paths = seed_reachability(g)
    assert len(g) == len(f) == 8
    assert counts[3] == counts[6] == 2
    assert counts[7] == counts[99] == counts[1] == 0
    assert paths[6] == [[1, 3, 4, 5, 6], [2, 3, 4, 5, 6]]
    indexed = f.set_index("gid")
    assert indexed.loc[99, "isolated"]
    assert "seed_inflow_incomplete" in indexed.loc[99, "observation_flags"]
    assert pd.isna(indexed.loc[99, "observed_flow_ratio"])
    assert indexed.loc[7, "depth_boundary"] and indexed.loc[7, "month_end_window"]
    assert not indexed.loc[7, "flow_ratio_eligible"]
    assert not indexed.loc[1, "flow_ratio_eligible"]
    assert all(math.isfinite(v) for v in f.pagerank)
    assert all(math.isfinite(v) for v in f.betweenness)


def test_bidirectional_projection_and_cluster_money_conservation():
    g, a, stats, meta, _, _ = compute()
    projection = community_projection(g)
    assert projection[3][4]["weight"] == 1000000
    assert stats.n_nodes.sum() == len(g)
    assert stats.n_seed.sum() == 3
    assert set(a.gid) == set(g)
    assert meta["internal_minor"] + meta["intercluster_minor"] == 3500000


def test_business_results_independent_of_input_row_order():
    normal, shuffled = compute(), compute(True)
    for idx in [1, 2, 4]:
        assert_frame_equal(normal[idx], shuffled[idx])
    assert normal[3] == shuffled[3]


def test_self_loop_kept_as_money_not_as_counterparty():
    n = pd.DataFrame({"gid": [1], "depth": [0], "is_seed": [True]})
    tx = pd.DataFrame({"src": [1], "dst": [1], "sum_kzt": [5000.], "date": ["2026-07-01"]})
    e = tx.drop(columns="date").assign(n_tx=1, depth=1)
    n, e, tx, q = validate_tables(n, e, tx, period_start="2026-07-01", period_end="2026-07-31")
    g = build_graph(n, e)
    a, stats, metadata = detect_communities(g)
    f, _ = compute_features(g, tx, a, period_end=q["period_end"])
    assert community_projection(g).number_of_edges() == 0
    assert stats.sum_minor_internal.sum() == 500000
    assert f.iloc[0].in_degree == f.iloc[0].out_degree == 0
    assert f.iloc[0].in_minor == f.iloc[0].out_minor == 500000
    assert f.iloc[0].active_days == 1
    assert not f.iloc[0].isolated
    assert f.iloc[0].reachable_seeds == 0
    assert "self_transfer" in f.iloc[0].observation_flags


def test_zero_edge_graph_has_singleton_clusters_and_finite_metrics():
    n, e, tx, _ = fixture()
    g = build_graph(n, e.iloc[:0])
    a, stats, _ = detect_communities(g)
    f, _ = compute_features(g, tx.iloc[:0], a, period_end="2026-07-31")
    assert len(stats) == len(n)
    assert f.isolated.all() and (f.betweenness == 0).all()
    assert math.isclose(f.pagerank.sum(), 1.)


def test_month_end_is_declared_window_not_last_observed_date():
    n, e, tx, _ = fixture()
    g = build_graph(n, e)
    a, _, _ = detect_communities(g)
    tx["date"] = pd.Timestamp("2026-07-29")
    f, _ = compute_features(g, tx, a, period_end="2026-07-31")
    assert not f.month_end_window.any()


def test_return_to_seed_and_four_hop_boundary_are_not_overcounted():
    # A cycle returns to two different seeds; a second route reaches node 3.
    g = nx.DiGraph()
    g.add_nodes_from((i, {"is_seed": i in (1, 2)}) for i in range(1, 8))
    g.add_edges_from([(1, 3), (2, 3), (3, 4), (4, 1), (4, 2),
                      (1, 4), (4, 5), (5, 6), (6, 7)])
    counts, paths = seed_reachability(g)
    assert counts[1] == counts[2] == 1  # The other seed, never itself.
    assert paths[1] == [[2, 3, 4, 1]]
    assert paths[2] == [[1, 4, 2]]
    assert counts[3] == 2  # Distinct origins, not the number of possible routes.
    assert counts[6] == 2 and counts[7] == 1  # Second seed needs five hops to 7.
    assert paths[7] == [[1, 4, 5, 6, 7]]
    zero_counts, zero_paths = seed_reachability(g, max_hops=0)
    assert not any(zero_counts.values()) and not any(zero_paths.values())


def test_path_examples_cap_does_not_cap_seed_count():
    g = nx.DiGraph()
    g.add_nodes_from((i, {"is_seed": i < 5}) for i in range(1, 6))
    g.add_edges_from((i, 5) for i in range(4, 0, -1))
    counts, paths = seed_reachability(g)
    assert counts[5] == 4
    assert paths[5] == [[1, 5], [2, 5], [3, 5]]
