"""Opt-in acceptance on locally supplied data; no transactions are committed."""
import os
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from moneygraph.io.load import load_dataset
from moneygraph.analytics.graph import build_graph
from moneygraph.analytics.communities import detect_communities
from moneygraph.analytics.features import compute_features


@pytest.mark.skipif(not os.environ.get("MONEYGRAPH_DATA_DIR"), reason="Local case data not supplied")
def test_official_dataset_coverage_money_and_determinism():
    data = load_dataset(Path(os.environ["MONEYGRAPH_DATA_DIR"]),
                        period_start="2026-07-01", period_end="2026-07-31")
    graph = build_graph(data.nodes, data.edges)
    assignment, stats, meta = detect_communities(graph)
    features, fm = compute_features(graph, data.transactions, assignment, period_end="2026-07-31")
    assert len(features) == features.gid.nunique() == 2248
    assert features.isolated.sum() == 19
    assert features.depth_boundary.sum() == 444
    assert data.quality["duplicate_transaction_rows_preserved"] == 97
    assert fm["n_components"] == 35
    assert stats.n_nodes.sum() == 2248 and stats.n_seed.sum() == 81
    assert meta["internal_minor"] + meta["intercluster_minor"] == 36589001201
    assert features.in_minor.sum() == features.out_minor.sum() == 36589001201
    assert features.in_tx.sum() == features.out_tx.sum() == 4840
    assert not features[["pagerank", "betweenness"]].isna().any().any()
    assert not features.loc[features.is_seed | features.depth_boundary, "flow_ratio_eligible"].any()
    reshuffled = build_graph(data.nodes.sample(frac=1, random_state=13),
                             data.edges.sample(frac=1, random_state=13))
    a2, s2, _ = detect_communities(reshuffled)
    f2, _ = compute_features(reshuffled, data.transactions.sample(frac=1, random_state=13),
                             a2, period_end="2026-07-31")
    assert_frame_equal(assignment, a2)
    assert_frame_equal(stats, s2)
    assert_frame_equal(features, f2)
    for row in features.itertuples():
        for path in row.seed_paths:
            assert len(path) <= 5 and path[-1] == row.gid
            assert path[0] != row.gid and graph.nodes[path[0]]["is_seed"]
            assert all(graph.has_edge(a, b) for a, b in zip(path, path[1:]))
