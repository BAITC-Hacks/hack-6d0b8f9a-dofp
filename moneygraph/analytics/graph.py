"""Canonical directed graph plus a separate community projection."""
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
