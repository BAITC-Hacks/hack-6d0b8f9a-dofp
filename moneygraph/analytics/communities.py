"""Component-wise Louvain; structural groups are not criminal labels."""
import math
import networkx as nx
import pandas as pd

from .graph import community_projection


def detect_communities(graph, *, resolution=1.0, seed=42):
    """Return (assignment DataFrame, numeric cluster stats DataFrame, metadata)."""
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("resolution must be positive and finite")
    projection = community_projection(graph)
    groups = []
    for component in sorted(nx.connected_components(projection), key=min):
        if len(component) == 1:
            groups.append(set(component))
        else:
            local = projection.subgraph(sorted(component)).copy()
            groups.extend(nx.community.louvain_communities(
                local, weight="weight", resolution=resolution, seed=seed))
    groups.sort(key=min)
    mapping = {gid: cid for cid, group in enumerate(groups) for gid in group}
    internal, external = [0] * len(groups), 0
    for src, dst, attrs in graph.edges(data=True):
        if mapping[src] == mapping[dst]:
            internal[mapping[src]] += attrs["sum_minor"]
        else:
            external += attrs["sum_minor"]
    assignment = pd.DataFrame([{"gid": gid, "cluster_id": mapping[gid]}
                               for gid in sorted(graph)], columns=["gid", "cluster_id"])
    stats = pd.DataFrame([
        {"cluster_id": cid, "n_nodes": len(group),
         "n_seed": sum(graph.nodes[g]["is_seed"] for g in group),
         "sum_minor_internal": internal[cid]}
        for cid, group in enumerate(groups)],
        columns=["cluster_id", "n_nodes", "n_seed", "sum_minor_internal"])
    metadata = {"algorithm": "component-wise-louvain", "resolution": resolution,
                "seed": seed, "networkx_version": nx.__version__,
                "self_loops_excluded_from_projection": nx.number_of_selfloops(graph),
                "intercluster_minor": external, "internal_minor": sum(internal),
                "total_minor": external + sum(internal)}
    return assignment, stats, metadata
