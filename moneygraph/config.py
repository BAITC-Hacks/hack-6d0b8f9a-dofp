"""Explicit, versioned inputs to a reproducible batch calculation."""
from dataclasses import asdict, dataclass, field
from datetime import date
from math import isfinite

from .analytics.roles import RolePolicy


@dataclass(frozen=True)
class PipelineConfig:
    period_start: date = date(2026, 7, 1)
    period_end: date = date(2026, 7, 31)
    resolution: float = 1.0
    seed: int = 42
    betweenness_k: int = 128
    top_limit: int = 30
    min_amount_minor: int = 500_000
    policy: RolePolicy = field(default_factory=RolePolicy)

    def __post_init__(self):
        if any(type(d) is not date for d in (self.period_start, self.period_end)):
            raise ValueError("Period boundaries must be calendar dates")
        if self.period_start > self.period_end:
            raise ValueError("period_start must not exceed period_end")
        for name, minimum in (("seed", 0), ("betweenness_k", 1),
                              ("top_limit", 20), ("min_amount_minor", 1)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if isinstance(self.resolution, bool) or not isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError("resolution must be positive and finite")
        if not isinstance(self.policy, RolePolicy):
            raise ValueError("policy must be a validated RolePolicy")

    def record(self) -> dict:
        result = asdict(self)
        result.update(period_start=self.period_start.isoformat(),
                      period_end=self.period_end.isoformat(), max_depth=4)
        return result
