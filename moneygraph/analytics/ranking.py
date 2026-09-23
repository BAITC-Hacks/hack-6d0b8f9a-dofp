"""Batch scoring: percentiles use the complete input, never a filtered UI view."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from typing import Iterable

from .explanations import evidence
from .roles import NodeFeatures, RoleAssignment, RolePolicy, RULES_VERSION, assign_role, saturate


@dataclass(frozen=True)
class ScoredNode:
    features: NodeFeatures
    assignment: RoleAssignment
    priority_score: float
    evidence: str
    factors: dict[str, float]
    contributions: dict[str, float]
    policy: RolePolicy

    def csv_record(self) -> dict:
        """Exactly the six mandatory nodes_roles columns; no output I/O."""
        return {"gid": self.features.gid, "role": self.assignment.role,
                "role_score": self.assignment.role_score, "cluster_id": self.features.cluster_id,
                "priority_score": self.priority_score, "evidence": self.evidence}

    def explanation_record(self) -> dict:
        record = asdict(self)
        if self.features.last_in_date is not None:
            record["features"]["last_in_date"] = self.features.last_in_date.isoformat()
        record["features"]["gid"] = str(self.features.gid)
        for key in ("in_minor", "out_minor"):
            record["features"][key] = str(getattr(self.features, key))
        record["currency"] = "KZT"
        record["scale"] = 2
        record["rules_version"] = RULES_VERSION
        return record


def positive_percentiles(values: list[float | int]) -> list[float]:
    """Average 1-based rank / positive population size. Zero maps to zero.

    Keep integer amounts exact when ordering: no float cast of minor units.
    """
    if any(not isfinite(v) or v < 0 for v in values):
        raise ValueError("Percentiles require finite nonnegative values")
    positive = sorted(v for v in values if v > 0)
    # Standard-library searches determine the tied range; cache by distinct
    # value. Unlike a float array, direct integer comparison preserves tiyns.
    ranks = {value: (bisect_left(positive, value) + 1 + bisect_right(positive, value))
                   / (2 * len(positive)) for value in set(positive)}
    return [ranks.get(v, 0.0) for v in values]


def score_nodes(features: Iterable[NodeFeatures], *, period_end: date,
                policy: RolePolicy = RolePolicy()) -> list[ScoredNode]:
    """Return gid-sorted scores; preserve every input node including isolates."""
    nodes = sorted(features, key=lambda n: n.gid)
    if len({n.gid for n in nodes}) != len(nodes):
        raise ValueError("Duplicate gid in scoring input")
    if type(period_end) is not date:
        raise ValueError("period_end must be a calendar date")
    centrality = positive_percentiles([n.betweenness for n in nodes])
    volumes = positive_percentiles([max(n.in_minor, n.out_minor) for n in nodes])
    weights = {"seed_reach": 0.35, "role_support": 0.25, "betweenness": 0.25, "volume": 0.15}
    result = []
    for node, central, volume in zip(nodes, centrality, volumes):
        assignment = assign_role(node, period_end=period_end,
                                 betweenness_percentile=central, policy=policy)
        factors = {"seed_reach": saturate(node.reachable_seeds, 3),
                   "role_support": assignment.support, "betweenness": central, "volume": volume}
        contributions = {key: weights[key] * value for key, value in factors.items()}
        priority = min(1.0, max(0.0, sum(contributions.values())))
        result.append(ScoredNode(node, assignment, priority, evidence(node, assignment), factors, contributions, policy))
    return result


def top_records(scored: Iterable[ScoredNode], *, limit: int = 30) -> list[dict]:
    """At least 20 rows for populations of 20+; all rows for smaller fixtures."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 20:
        raise ValueError("top-list limit must be an integer >=20")
    nodes = list(scored)
    if len({n.features.gid for n in nodes}) != len(nodes):
        raise ValueError("Duplicate gid in ranked input")
    ordered = sorted(nodes, key=lambda n: (-n.priority_score, n.features.gid))[:limit]
    return [{"rank": rank, "gid": n.features.gid, "role": n.assignment.role,
             "priority_score": n.priority_score,
             "why": (n.evidence + f" Приоритет: seed={n.contributions['seed_reach']:.3f}; "
                     f"роль={n.contributions['role_support']:.3f}; "
                     f"посредничество={n.contributions['betweenness']:.3f}; "
                     f"оборот={n.contributions['volume']:.3f}.")}
            for rank, n in enumerate(ordered, 1)]
