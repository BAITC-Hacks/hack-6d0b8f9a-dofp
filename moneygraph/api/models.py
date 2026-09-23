"""API-owned view models; core contracts can be adapted via model_dump/asdict."""
from decimal import Decimal
from datetime import date, datetime
from numbers import Integral
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal['consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral']
ROLES = ('consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral')


def exact_integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (Integral, str)) or not re.fullmatch(r'-?\d+', str(value)):
        raise ValueError('Expected an exact integer, not a float or boolean')
    return int(value)


def calendar_date(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None or any((value.hour, value.minute, value.second, value.microsecond, getattr(value, 'nanosecond', 0))):
            raise ValueError('Transaction date must have calendar-day precision')
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Transaction date must be YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


def gid_string(value: Any) -> str:
    if isinstance(value, bool) or not (isinstance(value, Integral) or isinstance(value, str)):
        raise ValueError('gid must be an int64 or an integer string, never a float')
    if not re.fullmatch(r'-?\d+', str(value)):
        raise ValueError('gid must contain a decimal integer')
    integer = int(value)
    if not -(2**63) <= integer < 2**63:
        raise ValueError('gid outside int64 range')
    return str(integer)


def minor_units(value: Any) -> str:
    amount = Decimal(str(value)) * 100
    if not amount.is_finite():
        raise ValueError('KZT amount must be finite')
    nearest = amount.to_integral_value()
    # Accept only insignificant floating point aggregation noise, never a fraction of a tiyn.
    if abs(amount - nearest) > Decimal('0.000001'):
        raise ValueError('KZT amount must be finite and exact to two decimal places')
    return str(int(nearest))


class Node(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    gid: str
    role: Role
    role_score: float = Field(ge=0, le=1)
    priority_score: float = Field(ge=0, le=1)
    cluster_id: int = Field(ge=0)
    evidence: str = Field(min_length=1, max_length=200)
    depth: int | None = Field(default=None, ge=0, le=4)
    is_seed: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    paths: list[list[str]] = Field(default_factory=list)
    rule_id: str | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    rule_details: dict[str, Any] = Field(default_factory=dict)

    @field_validator('cluster_id', 'depth', mode='before')
    @classmethod
    def exact_count(cls, v: Any) -> Any:
        return None if v is None else exact_integer(v)

    @field_validator('gid', mode='before')
    @classmethod
    def normalize_gid(cls, v: Any) -> str:
        return gid_string(v)

    @field_validator('evidence')
    @classmethod
    def nonblank_evidence(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('evidence cannot be blank')
        return v

    @field_validator('paths', mode='before')
    @classmethod
    def normalize_paths(cls, v: Any) -> list[list[str]]:
        return [[gid_string(gid) for gid in path] for path in (v or [])]


class Edge(BaseModel):
    model_config = ConfigDict(extra='ignore')
    src: str
    dst: str
    sum_minor: str = Field(pattern=r'^0*[1-9]\d*$')
    n_tx: int = Field(ge=1)

    @field_validator('n_tx', mode='before')
    @classmethod
    def exact_count(cls, v: Any) -> int:
        return exact_integer(v)

    @field_validator('src', 'dst', mode='before')
    @classmethod
    def normalize_gid(cls, v: Any) -> str:
        return gid_string(v)

    @field_validator('sum_minor', mode='before')
    @classmethod
    def normalize_minor(cls, v: Any) -> str:
        return str(v)


class Transaction(BaseModel):
    model_config = ConfigDict(extra='ignore')
    src: str
    dst: str
    date: str
    sum_minor: str = Field(pattern=r'^0*[1-9]\d*$')
    tx_ref: str

    @field_validator('src', 'dst', mode='before')
    @classmethod
    def normalize_gid(cls, v: Any) -> str:
        return gid_string(v)

    @field_validator('date', mode='before')
    @classmethod
    def valid_date(cls, v: str) -> str:
        return calendar_date(v)
