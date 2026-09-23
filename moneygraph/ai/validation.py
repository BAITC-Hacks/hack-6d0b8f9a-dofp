"""Deterministic checks supplement, not replace, semantic review."""
from decimal import Decimal
import re

from pydantic import ValidationError

from .context import CHECKS, ClientContext, json_bytes
from .errors import InvalidExplanation
from .models import ModelExplanation
from .provider import strict_json

_NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?")
_ALIAS = re.compile(r"Клиент_[A-Z]+")


def _numbers(text):
    return {Decimal(number.replace(",", ".")) for number in _NUMBER.findall(text)}


def validate_explanation(raw: str, context: ClientContext) -> ModelExplanation:
    try:
        result = ModelExplanation.model_validate(strict_json(raw))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise InvalidExplanation("Response schema") from None
    facts = {fact.id: fact for fact in context.facts}
    by_key = {fact.key: fact.id for fact in context.facts}
    expected_limits = {fact.key.split(":", 1)[1]: fact.id for fact in context.facts
                       if fact.key.startswith("limitation:")}
    allowed_aliases = {alias for alias, _ in context.alias_map}
    sections = (result.summary, result.reasons, result.alternative_hypotheses,
                result.next_checks, result.limitations)
    for section in sections:
        for statement in section:
            refs = statement.fact_ids
            if len(refs) != len(set(refs)) or not set(refs) <= facts.keys():
                raise InvalidExplanation("Unknown or repeated citation")
            text = statement.text
            if (text != text.strip() or not re.search(r"[А-Яа-яЁё]", text)
                    or re.search(r"https?://|[<>`]|[\x00-\x08\x0b-\x1f]", text)):
                raise InvalidExplanation("Unsafe or non-Russian output")
            if not set(_ALIAS.findall(text)) <= allowed_aliases:
                raise InvalidExplanation("Unknown client alias")
            allowed_numbers = set()
            for ref in refs:
                allowed_numbers |= _numbers(json_bytes(facts[ref].value).decode("utf-8"))
            if not _numbers(text) <= allowed_numbers:
                raise InvalidExplanation("Number not in cited facts")
            # Guard direct accusations. Nuanced unsupported claims are checked by
            # the separate model review; lexical checks cannot prove entailment.
            if re.search(r"(?:является|признан|точно)\s+(?:преступник|мошенник)|отмывает\s+деньги", text, re.I):
                raise InvalidExplanation("Accusation")
    actual_limits = [item.code for item in result.limitations]
    if len(actual_limits) != len(set(actual_limits)) or set(actual_limits) != expected_limits.keys():
        raise InvalidExplanation("Missing or invented limitation")
    for item in result.limitations:
        if expected_limits[item.code] not in item.fact_ids:
            raise InvalidExplanation("Limitation has unrelated evidence")
    reason_refs = {ref for item in result.reasons for ref in item.fact_ids}
    if not {by_key[key] for key in ("role", "role_support", "role_score", "priority", "contributions", "caps")} <= reason_refs:
        raise InvalidExplanation("Role or priority explanation incomplete")
    if len({check.action for check in result.next_checks}) != len(result.next_checks):
        raise InvalidExplanation("Duplicate next action")
    for check in result.next_checks:
        if (check.availability != CHECKS[check.action][0]
                or by_key["action:" + check.action] not in check.fact_ids
                or not any(not facts[ref].key.startswith("action:") for ref in check.fact_ids)):
            raise InvalidExplanation("Action availability or evidence mismatch")
    for hypothesis in result.alternative_hypotheses:
        if not hypothesis.text.startswith("Гипотеза:"):
            raise InvalidExplanation("Unmarked hypothesis")
    return result
