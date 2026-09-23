"""Strict response contract. Pydantic is already provided by the serve extra."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Statement(StrictModel):
    text: str = Field(min_length=10, max_length=700)
    fact_ids: list[str] = Field(min_length=1, max_length=12)


class Hypothesis(Statement):
    status: Literal["hypothesis"]


class NextCheck(Statement):
    availability: Literal["in_product", "request_data"]
    action: Literal["inspect_transactions", "inspect_paths", "inspect_community",
                    "request_payment_purpose", "request_relationship_context",
                    "request_full_history", "request_intraday_times"]


class Limitation(Statement):
    code: str = Field(min_length=1, max_length=80)


class ModelExplanation(StrictModel):
    # One statement per sentence keeps citations local, including the summary.
    summary: list[Statement] = Field(min_length=2, max_length=3)
    reasons: list[Statement] = Field(min_length=2, max_length=5)
    alternative_hypotheses: list[Hypothesis] = Field(min_length=1, max_length=2)
    next_checks: list[NextCheck] = Field(min_length=2, max_length=3)
    limitations: list[Limitation] = Field(min_length=1, max_length=16)


class Review(StrictModel):
    supported: bool
    issues: list[str] = Field(max_length=12)
