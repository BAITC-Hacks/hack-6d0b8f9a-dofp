"""Canonical directed graph plus a separate community projection."""
from numbers import Integral
import re

import networkx as nx


def build_graph(nodes, edges, *, max_depth=4):
    """Inputs must be normalized by validate_tables/load_dataset."""
    graph = nx.DiGraph(max_depth=max_depth)
    for r in nodes.sort_values("gid").itertuples(index=False):
        graph.add_node(int(r.gid), gid=int(r.gid), depth=int(r.depth), is_seed=bool(r.is_seed))
    for r in edges.sort_values(["src", "dst"]).itertuples(index=False):
        if r.src not in graph or r.dst not in graph:
            raise ValueError("Unknown endpoint: validate tables before building graph")
        graph.add_edge(int(r.src), int(r.dst), sum_minor=int(r.sum_minor),
                       n_tx=int(r.n_tx), depth=int(r.depth))
    return graph


def community_projection(graph):
    """Reciprocal money is added, self-loops excluded, all nodes retained."""
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph.nodes))
    for src, dst, attrs in sorted(graph.edges(data=True)):
        if src == dst:
            continue
        previous = projection.get_edge_data(src, dst, {}).get("weight", 0)
        projection.add_edge(src, dst, weight=previous + attrs["sum_minor"])
    return projection


def _path_gid(value):
    if isinstance(value, Integral) and not isinstance(value, bool):
        gid = int(value)
    elif isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        gid = int(value)
    else:
        raise ValueError("Path identifiers must be exact integers or decimal strings")
    if not -(2**63) <= gid < 2**63:
        raise ValueError("Path identifier outside int64")
    return gid


def seed_path_view(graph, gid, paths, *, path_index=0, max_hops=4):
    """Materialize ONE saved directed seed path from the full snapshot graph.

    Uses integer node keys, as in build_graph. paths must be the chosen client's
    saved seed_paths (A) / paths (C), from the same snapshot. No neighborhood
    limit, role/cluster filter or ranking may remove a path node. The response
    is JSON-safe and contains only consecutive path edges, not an induced graph.
    None means routes unavailable; [] means there are no saved route examples.
    Does not calculate new routes, assign roles or infer temporal money flow.
    """
    if not graph.is_directed() or graph.is_multigraph():
        raise ValueError("A canonical directed simple graph is required")
    gid = _path_gid(gid)
    if gid not in graph:
        raise KeyError(gid)
    if not isinstance(path_index, Integral) or isinstance(path_index, bool) or path_index < 0:
        raise ValueError("path_index must be a nonnegative integer")
    if not isinstance(max_hops, Integral) or isinstance(max_hops, bool) or max_hops < 1:
        raise ValueError("max_hops must be a positive integer")
    path_index = int(path_index)
    saved = None if paths is None else list(paths)
    n_paths = None if saved is None else len(saved)
    result = {"schema_version": "seed-path-view.v1", "view_mode": "seed_path",
              "focus_gid": str(gid), "state": "paths_unavailable" if saved is None else "no_saved_paths",
              "path_index": None, "available_paths": n_paths, "path": [],
              "nodes": [], "edges": [], "total_nodes": 1, "total_edges": 0,
              "hidden_nodes": 0, "hidden_edges": 0, "truncated": False,
              "warnings": ["date_only", "structural_path_only", "edge_totals_cover_reporting_period"]}

    def node_record(key, step):
        attrs = graph.nodes[key]
        depth = attrs.get("depth")
        return {"gid": str(key), "depth": None if depth is None else int(depth),
                "is_seed": bool(attrs["is_seed"]), "step_index": step}

    if not saved:
        if path_index != 0:
            raise IndexError("No saved path at this index")
        result["nodes"] = [node_record(gid, None)]
        return result
    if path_index >= len(saved):
        raise IndexError("No saved path at this index")
    route = [_path_gid(key) for key in saved[path_index]]
    if not 2 <= len(route) <= max_hops + 1:
        raise ValueError("Saved path must contain between one and max_hops edges")
    if len(set(route)) != len(route):
        raise ValueError("Saved path must not repeat nodes")
    if route[-1] != gid:
        raise ValueError("Saved path must end at the selected client")
    if any(key not in graph for key in route):
        raise ValueError("Saved path contains a node missing from this snapshot")
    if not graph.nodes[route[0]]["is_seed"]:
        raise ValueError("Saved path must start at an initial client")
    pairs = list(zip(route, route[1:]))
    if any(not graph.has_edge(src, dst) for src, dst in pairs):
        raise ValueError("Saved path contains a missing directed edge")

    edges = []
    for step, (src, dst) in enumerate(pairs, start=1):
        attrs = graph.edges[src, dst]
        # build_graph has already reconciled exact amounts and operation counts.
        amount, count = attrs["sum_minor"], attrs["n_tx"]
        if any(not isinstance(v, Integral) or isinstance(v, bool) or v <= 0
               for v in (amount, count)):
            raise ValueError("Path edges require positive integer tiyn and counts")
        edges.append({"src": str(src), "dst": str(dst), "sum_minor": str(int(amount)),
                      "n_tx": int(count), "step_index": step,
                      "edge_id": f"{src}->{dst}"})
    result.update(state="found", path_index=path_index, path=[str(key) for key in route],
                  nodes=[node_record(key, step) for step, key in enumerate(route)],
                  edges=edges, total_nodes=len(route), total_edges=len(edges))
    result["warnings"].append("seed_inflow_incomplete")
    if any(graph.nodes[key].get("depth") == graph.graph.get("max_depth", 4) for key in route):
        result["warnings"].append("depth_boundary")
    return result
