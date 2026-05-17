"""Cross-model verifier (D-decision: opt-in via CROSS_MODEL_VERIFY=true).

Runs a SECOND LLM provider against the same source text after the primary
extraction and surfaces scalar-field disagreements as ValidationWarnings.

Design notes:
- Compares ONLY scalar fields with cheap, deterministic equality rules.
  Lists like `exclusions` and `coverage_limits` are too noisy for direct
  cross-model compare (different phrasings, different orderings) - we skip
  them rather than emit false-positive warnings.
- A None value on either side -> NOT a disagreement (treat as no signal).
  This avoids flagging cases where one model simply couldn't find the field.
- Money compared at 0.01 tolerance (cents). Dates compared at exact day.
- Failure is non-fatal at the caller level; this module just returns the list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.extractors.llm.base import ExtractionResult, LLMProvider


# Fields we cross-check. Scalars only - skip noisy lists/dicts.
_VERIFY_FIELDS: tuple[str, ...] = (
    "policy_number",
    "insured_name",
    "insurer_name",
    "effective_date",
    "expiration_date",
    "premium_amount",
    "face_amount",
)

_MONEY_FIELDS: frozenset[str] = frozenset({"premium_amount", "face_amount"})
_DATE_FIELDS: frozenset[str] = frozenset({"effective_date", "expiration_date"})
_MONEY_TOLERANCE: Decimal = Decimal("0.01")


@dataclass
class Disagreement:
    """A single field where primary and secondary providers disagree."""

    field_name: str
    primary_value: Any
    secondary_value: Any


def _norm_string(v: Any) -> str | None:
    if v is None:
        return None
    return str(v).strip().casefold()


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _values_disagree(field: str, primary: Any, secondary: Any) -> bool:
    """Return True iff the two values represent a true disagreement.

    Rules:
      - Either side is None -> NOT a disagreement (no signal from that model).
      - Money fields: parse to Decimal, |a-b| > 0.01 -> disagreement.
      - Date fields: parse to date, exact-day mismatch -> disagreement.
      - Everything else: normalized-string (strip + casefold) compare.
    """
    if primary is None or secondary is None:
        return False

    if field in _MONEY_FIELDS:
        p = _to_decimal(primary)
        s = _to_decimal(secondary)
        if p is None or s is None:
            # Couldn't parse -> fall back to string compare so we don't silently miss
            return _norm_string(primary) != _norm_string(secondary)
        return abs(p - s) > _MONEY_TOLERANCE

    if field in _DATE_FIELDS:
        p = _to_date(primary)
        s = _to_date(secondary)
        if p is None or s is None:
            return _norm_string(primary) != _norm_string(secondary)
        return p != s

    # Strings: policy_number, insured_name, insurer_name
    return _norm_string(primary) != _norm_string(secondary)


def verify_extraction(
    primary_result: ExtractionResult,
    full_text: str,
    secondary: LLMProvider,
) -> list[Disagreement]:
    """Run `secondary` against `full_text` and return scalar-field disagreements.

    Args:
        primary_result: The ExtractionResult from the primary LLM (already done).
        full_text: The same document text fed to the primary.
        secondary: The OTHER LLM provider (whatever wasn't used as primary).

    Returns:
        A list of Disagreement objects (one per disagreeing field). Empty list
        means the two models agree on every cross-checked field (or one side
        was None for all of them).
    """
    secondary_result = secondary.extract_fields(full_text)
    primary_fields = primary_result.fields or {}
    secondary_fields = secondary_result.fields or {}

    disagreements: list[Disagreement] = []
    for field in _VERIFY_FIELDS:
        primary_value = primary_fields.get(field)
        secondary_value = secondary_fields.get(field)
        if _values_disagree(field, primary_value, secondary_value):
            disagreements.append(
                Disagreement(
                    field_name=field,
                    primary_value=primary_value,
                    secondary_value=secondary_value,
                )
            )
    return disagreements
