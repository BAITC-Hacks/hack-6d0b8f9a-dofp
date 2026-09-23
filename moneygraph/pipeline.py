"""Orchestrate existing graph/scoring modules; keep algorithms in their owners' modules."""
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import platform
from time import perf_counter

from .analytics.communities import detect_communities
from .analytics.features import compute_features
from .analytics.graph import build_graph
from .analytics.ranking import score_nodes
from .analytics.roles import RULES_VERSION, from_feature_record
from .config import PipelineConfig
from .io.exports import json_safe, write_outputs
from .io.load import load_dataset
from .io.snapshots import AnalysisSnapshot, SCHEMA_VERSION, publish_snapshot


def code_hashes() -> dict[str, str]:
    """Hash calculation sources, including local uncommitted changes, not only HEAD."""
    root = Path(__file__).parent
    paths = [*root.glob("*.py"), *root.glob("analytics/*.py"), *root.glob("io/*.py")]
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def run_pipeline(data_dir: Path, out_dir: Path, *, config: PipelineConfig = PipelineConfig()) -> AnalysisSnapshot:
    start = perf_counter()
    data = load_dataset(data_dir, period_start=config.period_start.isoformat(),
                        period_end=config.period_end.isoformat(), max_depth=4,
                        min_amount_minor=config.min_amount_minor)
    if data.nodes.empty:
        raise ValueError("The analysis requires at least one node")
    loaded = perf_counter()
    graph = build_graph(data.nodes, data.edges, max_depth=4)
    assignment, clusters, community_meta = detect_communities(graph, resolution=config.resolution, seed=config.seed)
    features, feature_meta = compute_features(graph, data.transactions, assignment,
                                             period_end=config.period_end.isoformat(), max_hops=4,
                                             betweenness_k=config.betweenness_k, seed=config.seed)
    featured = perf_counter()
    scored = score_nodes((from_feature_record(row) for row in features.to_dict(orient="records")),
                         period_end=config.period_end, policy=config.policy)
    scored_at = perf_counter()
    identity = {"schema_version": SCHEMA_VERSION, "inputs": data.quality["input_sha256"],
                "config": json_safe(config.record()), "rules_version": RULES_VERSION,
                "code": code_hashes(), "environment": {
                    "python": platform.python_version(), "system": platform.system(),
                    "machine": platform.machine(),
                    "dependencies": {name: version(name) for name in
                                     ("numpy", "pandas", "pyarrow", "networkx", "scipy")}}}

    def write(directory: Path) -> dict:
        counts = write_outputs(directory, data=data, features=features, graph=graph,
                               clusters=clusters, scored=scored, top_limit=config.top_limit)
        return {"counts": counts, "currency": "KZT", "scale": 2,
                "graph_metadata": json_safe({"communities": community_meta, "features": feature_meta}),
                "warnings": data.quality["warnings"],
                "timings_seconds": {"load_validate": loaded-start, "graph_features": featured-loaded,
                                    "scoring": scored_at-featured, "through_exports": perf_counter()-start}}

    return publish_snapshot(out_dir, identity, write)
