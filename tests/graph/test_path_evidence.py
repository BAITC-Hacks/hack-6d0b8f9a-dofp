import copy
import json
import os

import networkx as nx
import pytest

from moneygraph.analytics.graph import build_graph, seed_path_view
from moneygraph.analytics.features import seed_reachability
from moneygraph.io.load import load_dataset


BASE = 100000000000000001  # Synthetic IDs above JavaScript's safe integer range.


def fixture():
    g = nx.DiGraph(max_depth=4)
    for offset in range(7):
        g.add_node(BASE + offset, depth=min(offset, 4), is_seed=offset in (0, 5, 6))
    # Main four-hop route, another seed, a reverse edge and an unrelated chord.
    for src, dst in [(0, 1), (1, 2), (2, 3), (3, 4), (5, 2), (2, 1), (1, 4)]:
        g.add_edge(BASE + src, BASE + dst, sum_minor=2**53 + 1, n_tx=2)
    return g


def main_route():
    return list(range(BASE, BASE + 5))


def test_four_hop_path_keeps_exact_edges_order_and_ids_without_chords():
    g = fixture()
    before = copy.deepcopy(g)
    view = seed_path_view(g, str(BASE + 4), [[str(x) for x in main_route()]])
    assert view["state"] == "found" and not view["truncated"]
    assert view["total_nodes"] == 5 and view["total_edges"] == 4
    assert view["hidden_nodes"] == view["hidden_edges"] == 0
    assert [n["gid"] for n in view["nodes"]] == list(map(str, main_route()))
    assert [n["step_index"] for n in view["nodes"]] == list(range(5))
    assert [(e["src"], e["dst"]) for e in view["edges"]] == list(zip(view["path"], view["path"][1:]))
    assert all(e["sum_minor"] == str(2**53 + 1) and e["n_tx"] == 2 for e in view["edges"])
    assert "depth_boundary" in view["warnings"]
    assert json.loads(json.dumps(view, allow_nan=False)) == view
    assert nx.utils.graphs_equal(before, g)


def test_select_second_saved_path_and_keep_its_index():
    g = fixture()
    paths = [main_route(), [BASE + 5, BASE + 2, BASE + 3, BASE + 4]]
    view = seed_path_view(g, BASE + 4, paths, path_index=1)
    assert view["path_index"] == 1 and view["available_paths"] == 2
    assert view["path"] == list(map(str, paths[1]))
    assert view["total_nodes"] == 4


def test_isolate_unknown_node_and_unavailable_routes_are_different():
    g = fixture()
    empty = seed_path_view(g, BASE + 6, [])
    missing = seed_path_view(g, BASE + 6, None)
    assert empty["state"] == "no_saved_paths" and empty["available_paths"] == 0
    assert missing["state"] == "paths_unavailable" and missing["available_paths"] is None
    assert empty["nodes"][0]["gid"] == str(BASE + 6) and empty["edges"] == []
    with pytest.raises(KeyError):
        seed_path_view(g, BASE + 999, [])


@pytest.mark.parametrize("route", [
    [], [BASE + 4], [BASE + 1, BASE + 2, BASE + 3, BASE + 4],
    [BASE, BASE + 1, BASE + 2], [BASE, BASE + 999, BASE + 4],
    [BASE, BASE + 2, BASE + 3, BASE + 4],
    [BASE, BASE + 1, BASE + 2, BASE + 1, BASE + 4],
    [BASE, BASE + 1, BASE + 2, BASE + 3, BASE + 5, BASE + 4],
    [float(BASE), BASE + 1, BASE + 4],
])
def test_invalid_or_stale_routes_fail_without_partial_graph(route):
    with pytest.raises(ValueError):
        seed_path_view(fixture(), BASE + 4, [route])


def test_reverse_direction_alone_is_not_enough():
    g = fixture()
    g.remove_edge(BASE + 1, BASE + 2)
    assert g.has_edge(BASE + 2, BASE + 1)
    with pytest.raises(ValueError, match="directed edge"):
        seed_path_view(g, BASE + 4, [main_route()])


@pytest.mark.parametrize("index", [-1, True, 0.5])
def test_bad_path_index_is_rejected(index):
    with pytest.raises(ValueError, match="path_index"):
        seed_path_view(fixture(), BASE + 4, [main_route()], path_index=index)


def test_out_of_range_index_is_rejected():
    with pytest.raises(IndexError):
        seed_path_view(fixture(), BASE + 4, [main_route()], path_index=1)


@pytest.mark.skipif(not os.environ.get("MONEYGRAPH_DATA_DIR"), reason="Local case data not supplied")
def test_every_saved_case_route_matches_canonical_graph():
    data = load_dataset(os.environ["MONEYGRAPH_DATA_DIR"],
                        period_start="2026-07-01", period_end="2026-07-31")
    g = build_graph(data.nodes, data.edges)
    _, examples = seed_reachability(g)
    checked = four_hop = empty = 0
    for gid, paths in examples.items():
        if not paths:
            assert seed_path_view(g, gid, paths)["state"] == "no_saved_paths"
            empty += 1
        for index, path in enumerate(paths):
            view = seed_path_view(g, gid, paths, path_index=index)
            assert view["path"] == list(map(str, path))
            assert len(view["nodes"]) == len(path)
            assert len(view["edges"]) == len(path) - 1
            for edge in view["edges"]:
                original = g.edges[int(edge["src"]), int(edge["dst"])]
                assert int(edge["sum_minor"]) == original["sum_minor"]
                assert edge["n_tx"] == original["n_tx"]
            checked += 1
            four_hop += len(path) == 5
    assert checked > 0 and four_hop > 0 and empty >= 19
