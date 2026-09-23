"""Explainable role hypotheses; no graph construction or probability claims."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from fractions import Fraction
from math import isfinite
from numbers import Integral, Real
from typing import Mapping

RULES_VERSION = "roles-v1.2"
ROLE_ORDER = ("coordinator", "consolidator", "distributor", "transit", "terminal")


@dataclass(frozen=True)
class NodeFeatures:
    """Scoring boundary. Money is integer minor units; dates are calendar dates.

    None means unavailable for optional upstream temporal features.
    Missing features disable the rules that require them, never imply zero.
    """
    gid: int
    depth: int
    is_seed: bool
    cluster_id: int
    in_degree: int
    out_degree: int
    in_minor: int
    out_minor: int
    in_tx: int
    out_tx: int
    reachable_seeds: int
    betweenness: float
    other_neighbor_clusters: int
    active_in_days: int | None = None
    last_in_date: date | None = None
    temporal_transit_confirmed: bool = False
    observation_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        signed = {"gid", "cluster_id"}
        integers = signed | {"depth", "in_degree", "out_degree", "in_minor", "out_minor",
                             "in_tx", "out_tx", "reachable_seeds", "other_neighbor_clusters"}
        for key in integers:
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError(f"{key} must be an integer")
            if key not in signed and value < 0:
                raise ValueError(f"{key} must be nonnegative")
            object.__setattr__(self, key, int(value))
        if not -(2**63) <= self.gid < 2**63 or self.cluster_id < 0:
            raise ValueError("gid must fit int64 and cluster_id must be assigned")
        if self.depth > 4 or type(self.is_seed) is not bool:
            raise ValueError("Expected depth 0..4 and a boolean is_seed")
        if self.is_seed != (self.depth == 0):
            raise ValueError("is_seed must agree with depth=0")
        if (isinstance(self.betweenness, bool) or not isinstance(self.betweenness, Real)
                or not isfinite(self.betweenness) or not 0 <= self.betweenness <= 1):
            raise ValueError("betweenness must be finite and normalized to [0,1]")
        object.__setattr__(self, "betweenness", float(self.betweenness))
        if type(self.temporal_transit_confirmed) is not bool:
            raise ValueError("temporal_transit_confirmed must be boolean")
        if not isinstance(self.observation_flags, tuple) or any(
                not isinstance(flag, str) or not flag for flag in self.observation_flags):
            raise ValueError("observation_flags must be a tuple of nonempty strings")
        for prefix in ("in", "out"):
            degree, amount, count = (getattr(self, f"{prefix}_{suffix}")
                                     for suffix in ("degree", "minor", "tx"))
            if (amount == 0) != (count == 0) or degree > count:
                raise ValueError(f"Inconsistent {prefix} amount/count/degree")
        if self.active_in_days is not None:
            if (isinstance(self.active_in_days, bool)
                    or not isinstance(self.active_in_days, Integral)
                    or not 0 <= self.active_in_days <= self.in_tx):
                raise ValueError("active_in_days must be an integer in [0,in_tx]")
            object.__setattr__(self, "active_in_days", int(self.active_in_days))
            if (self.active_in_days == 0) != (self.in_tx == 0):
                raise ValueError("active_in_days must agree with incoming transactions")
        if self.last_in_date is not None:
            if type(self.last_in_date) is not date or self.in_tx == 0:
                raise ValueError("last_in_date must be a date with observed incoming transactions")

    @property
    def depth_boundary(self) -> bool:
        # Match module A: known outgoing edges do not make boundary coverage complete.
        return self.depth == 4

    @property
    def isolated(self) -> bool:
        return self.in_tx == 0 and self.out_tx == 0


def from_feature_record(record: Mapping[str, object]) -> NodeFeatures:
    """Adapt a row from A's FeatureTable.to_dict(orient='records').

    Retains upstream warnings; does not compute any graph features. Dates and
    missing scalar dates are converted explicitly, additional columns ignored.
    """
    names = {f.name for f in fields(NodeFeatures)}
    values = {key: value for key, value in record.items() if key in names}
    value = values.get("last_in_date")
    if value is None or (isinstance(value, (date, Real)) and value != value):
        values["last_in_date"] = None
    elif isinstance(value, datetime):
        if value.tzinfo is not None or any((value.hour, value.minute, value.second, value.microsecond)):
            raise ValueError("last_in_date must have calendar-day precision without timezone")
        values["last_in_date"] = value.date()
    if "observation_flags" in values:
        if not isinstance(values["observation_flags"], (list, tuple)):
            raise ValueError("observation_flags must be a list or tuple")
        values["observation_flags"] = tuple(values["observation_flags"])
    try:
        node = NodeFeatures(**values)
    except TypeError as exc:
        raise ValueError(f"Incomplete or invalid feature record: {exc}") from exc
    for flag in ("depth_boundary", "isolated"):
        if flag in record and record[flag] != getattr(node, flag):
            raise ValueError(f"Upstream {flag} disagrees with node features")
    return node


@dataclass(frozen=True)
class RolePolicy:
    """Versioned engineering thresholds, not calibrated banking criteria."""
    minimum_support: float = 0.5
    consolidator_min_in: int = 3
    fan_ratio: float = 2.0
    distributor_min_out: int = 5
    min_seeds: int = 2
    flow_tolerance: float = 0.2
    coordinator_percentile: float = 0.9
    coordinator_other_clusters: int = 2
    terminal_window_days: int = 2
    ambiguity_gap: float = 0.1

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value <= 0:
                raise ValueError(f"Invalid policy value: {f.name}")
        for key in ("minimum_support", "coordinator_percentile", "ambiguity_gap", "flow_tolerance"):
            if getattr(self, key) > 1:
                raise ValueError(f"{key} must not exceed 1")
        for key in ("consolidator_min_in", "distributor_min_out", "min_seeds",
                    "coordinator_other_clusters", "terminal_window_days"):
            if not isinstance(getattr(self, key), Integral):
                raise ValueError(f"{key} must be an integer")


@dataclass(frozen=True)
class Candidate:
    role: str
    rule_id: str
    eligible: bool
    accepted: bool
    support: float
    conditions: dict[str, bool]
    terms: dict[str, float]


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    role_score: float
    support: float
    candidates: tuple[Candidate, ...]
    limitations: tuple[str, ...]
    caps: dict[str, float]


def saturate(value: float, threshold: float) -> float:
    return min(max(value, 0) / threshold, 1.0)


def assign_role(node: NodeFeatures, *, period_end: date, betweenness_percentile: float,
                policy: RolePolicy = RolePolicy()) -> RoleAssignment:
    """Evaluate every candidate before deterministic tie-breaking."""
    if type(period_end) is not date:
        raise ValueError("period_end must be a calendar date")
    if not isfinite(betweenness_percentile) or not 0 <= betweenness_percentile <= 1:
        raise ValueError("Invalid betweenness percentile")
    if node.last_in_date is not None and node.last_in_date > period_end:
        raise ValueError("Incoming date is after period_end")
    # Gate monetary comparisons exactly. A float can round a value just outside
    # the allowed window onto its boundary for large integer amounts.
    exact_ratio = Fraction(node.out_minor, node.in_minor) if node.in_minor else None
    tolerance = Fraction(str(policy.flow_tolerance))
    cutoff = period_end - timedelta(days=policy.terminal_window_days)
    limitations = ["date_only", "sampling_threshold", "partial_network"]
    limitations.extend(flag for flag in node.observation_flags if flag not in limitations)
    if node.depth_boundary:
        limitations.append("depth_boundary")
    if node.is_seed:
        limitations.append("seed_inflow_incomplete")
    if node.isolated:
        limitations.append("isolated")
    if node.out_minor > node.in_minor:
        limitations.append("out_exceeds_observed_in")
    if node.last_in_date is None and node.in_tx:
        limitations.append("incoming_date_unavailable")
    if node.active_in_days is None:
        limitations.append("active_in_days_unavailable")
    if node.last_in_date is not None and node.last_in_date > cutoff:
        limitations.append("month_end_window")
    candidates: list[Candidate] = []

    def add(role: str, conditions: dict[str, bool], terms: dict[str, float]) -> None:
        support = sum(terms.values()) / len(terms)
        eligible = all(conditions.values())
        candidates.append(Candidate(role, f"{RULES_VERSION}:{role}", eligible,
                                    eligible and support >= policy.minimum_support,
                                    support, conditions, terms))

    add("consolidator", {
        "enough_payers": node.in_degree >= policy.consolidator_min_in,
        "fan_in_or_seed_branches": (node.in_degree >= policy.fan_ratio * max(node.out_degree, 1)
                                    or node.reachable_seeds >= policy.min_seeds),
    }, {"payers": saturate(node.in_degree, 8), "seed_branches": saturate(node.reachable_seeds, 3),
        "fan_in": saturate(node.in_degree / max(node.out_degree, 1), 3)})
    add("distributor", {
        "enough_receivers": node.out_degree >= policy.distributor_min_out,
        "fan_out": node.out_degree >= policy.fan_ratio * max(node.in_degree, 1),
    }, {"receivers": saturate(node.out_degree, 15),
        "fan_out": saturate(node.out_degree / max(node.in_degree, 1), 3)})
    add("transit", {
        "both_directions": node.in_degree > 0 and node.out_degree > 0,
        "not_seed": not node.is_seed, "not_boundary": not node.depth_boundary,
        "flow_window": exact_ratio is not None and 1-tolerance <= exact_ratio <= 1+tolerance,
    }, {"flow_similarity": float(max(0, 1-abs(exact_ratio-1)/tolerance)) if exact_ratio is not None else 0.0,
        "counterparties": saturate(min(node.in_degree, node.out_degree), 3)})
    add("terminal", {
        # Counterparty degree excludes self-loops, but a self-transfer is still
        # an observed outgoing operation. Do not describe such a node as a sink.
        "incoming_only": node.in_degree > 0 and node.out_degree == 0 and node.out_tx == 0,
        "not_seed": not node.is_seed, "interior_depth": node.depth < 4,
        "complete_window": node.last_in_date is not None and node.last_in_date <= cutoff,
        "known_active_days": node.active_in_days is not None,
    }, {"incoming_transactions": saturate(node.in_tx, 5),
        "active_days": saturate(node.active_in_days or 0, 3)})
    add("coordinator", {
        "seed_branches": node.reachable_seeds >= policy.min_seeds,
        "both_directions": node.in_degree > 0 and node.out_degree > 0,
        "positive_betweenness": node.betweenness > 0,
        "high_betweenness": betweenness_percentile >= policy.coordinator_percentile,
        "neighbor_communities": node.other_neighbor_clusters >= policy.coordinator_other_clusters,
    }, {"betweenness_percentile": betweenness_percentile,
        "seed_branches": saturate(node.reachable_seeds, 3),
        "neighbor_communities": saturate(node.other_neighbor_clusters, 3)})
    accepted = sorted((c for c in candidates if c.accepted),
                      key=lambda c: (-c.support, ROLE_ORDER.index(c.role)))
    if not accepted:
        return RoleAssignment("peripheral", 0.0 if node.isolated else 0.2, 0.0,
                              tuple(candidates), tuple(dict.fromkeys(limitations)), {})
    best = accepted[0]
    caps: dict[str, float] = {}
    if best.role == "terminal":
        caps["terminal_observed_only"] = 0.5
    if best.role == "transit":
        caps["temporal_confirmed" if node.temporal_transit_confirmed else "no_temporal_confirmation"] = (
            0.75 if node.temporal_transit_confirmed else 0.55)
    if node.depth_boundary:
        caps["depth_boundary"] = 0.6
    if len(accepted) > 1 and best.support-accepted[1].support < policy.ambiguity_gap:
        caps["ambiguous_roles"] = 0.6
    return RoleAssignment(best.role, min(best.support, *caps.values()) if caps else best.support,
                          best.support, tuple(candidates), tuple(dict.fromkeys(limitations)), caps)
