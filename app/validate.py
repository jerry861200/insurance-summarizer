from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass
class ValidationWarningInfo:
    field_name: str | None  # None = document-level warning
    severity: str           # "info" | "warning" | "error"
    message: str


@dataclass
class ValidationResult:
    confidence: dict[str, float]                  # field_name -> [0.0, 1.0]
    warnings: list[ValidationWarningInfo]
    grounded_fields: dict[str, bool]              # field_name -> True if found verbatim in source


# Confidence tiers (per spec — see docs/design_rationale)
CONF_REGEX_GROUNDED_PAGE = 1.00     # regex + found in source + has page hint
CONF_GROUNDED_PAGE       = 0.95     # llm + found in source + has page hint
CONF_GROUNDED_NO_PAGE    = 0.85
CONF_FORMAT_NOT_GROUNDED = 0.55
CONF_INVALID_OR_NULL     = 0.0


def validate(
    fields: dict[str, Any],
    full_text: str,
    source_page_hints: dict[str, list[int]] | None = None,
    extraction_method_per_field: dict[str, str] | None = None,
) -> ValidationResult:
    """Validate extracted fields and return per-field confidence + warnings.

    Layers:
      1. Format checks (dates parse, money in range, strings non-empty)
      2. Cross-field checks (effective < expiration, etc.)
      3. Source-grounding (value appears verbatim — normalized for money/dates)
      4. Confidence scoring combining the above
    """
    source_page_hints = source_page_hints or {}
    extraction_method_per_field = extraction_method_per_field or {}

    warnings: list[ValidationWarningInfo] = []
    confidence: dict[str, float] = {}
    grounded: dict[str, bool] = {}

    full_text_lower = full_text.lower()

    # Per-field format + grounding
    for field_name, value in fields.items():
        if value is None or value == "" or value == [] or value == {}:
            confidence[field_name] = CONF_INVALID_OR_NULL
            grounded[field_name] = False
            continue

        # Format validation (per field type)
        format_ok, format_warnings = _format_check(field_name, value)
        warnings.extend(format_warnings)

        if not format_ok:
            confidence[field_name] = CONF_INVALID_OR_NULL
            grounded[field_name] = False
            continue

        # Source grounding
        is_grounded = _is_grounded_in_source(field_name, value, full_text, full_text_lower)
        grounded[field_name] = is_grounded
        has_page_hint = bool(source_page_hints.get(field_name))
        is_regex = extraction_method_per_field.get(field_name) == "regex"

        # Tier selection
        if is_grounded and has_page_hint and is_regex:
            confidence[field_name] = CONF_REGEX_GROUNDED_PAGE
        elif is_grounded and has_page_hint:
            confidence[field_name] = CONF_GROUNDED_PAGE
        elif is_grounded:
            confidence[field_name] = CONF_GROUNDED_NO_PAGE
        else:
            confidence[field_name] = CONF_FORMAT_NOT_GROUNDED
            warnings.append(ValidationWarningInfo(
                field_name=field_name,
                severity="warning",
                message=f"Value '{value!r}' not found verbatim in source text — possible LLM hallucination",
            ))

    # Cross-field validations
    warnings.extend(_cross_field_checks(fields))

    # Document-level: missing critical fields
    for critical in ("policy_number", "insured_name"):
        if critical not in fields or fields.get(critical) in (None, ""):
            warnings.append(ValidationWarningInfo(
                field_name=critical,
                severity="error",
                message=f"Critical field '{critical}' missing from extraction",
            ))

    return ValidationResult(confidence=confidence, warnings=warnings, grounded_fields=grounded)


# --------------- helpers ---------------

def _format_check(field_name: str, value: Any) -> tuple[bool, list[ValidationWarningInfo]]:
    """Returns (is_valid_format, warnings)."""
    warnings: list[ValidationWarningInfo] = []

    if field_name in ("effective_date", "expiration_date"):
        # Should be ISO date string or date object
        if isinstance(value, date):
            return True, []
        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return True, []
            except ValueError:
                warnings.append(ValidationWarningInfo(
                    field_name=field_name,
                    severity="warning",
                    message=f"Date '{value}' is not valid ISO 8601 (YYYY-MM-DD)",
                ))
                return False, warnings
        return False, warnings

    if field_name in ("premium_amount", "face_amount"):
        try:
            d = Decimal(str(value))
            if d <= 0:
                warnings.append(ValidationWarningInfo(
                    field_name=field_name,
                    severity="warning",
                    message=f"Money value {value} must be > 0",
                ))
                return False, warnings
            if d > Decimal("1e9"):
                warnings.append(ValidationWarningInfo(
                    field_name=field_name,
                    severity="warning",
                    message=f"Money value {value} suspiciously large (> $1B)",
                ))
                return False, warnings
            return True, []
        except (InvalidOperation, ValueError, TypeError):
            warnings.append(ValidationWarningInfo(
                field_name=field_name,
                severity="warning",
                message=f"Money value {value!r} not numeric",
            ))
            return False, warnings

    if field_name == "policy_number":
        s = str(value).strip()
        if not s:
            return False, warnings
        if len(s) < 4:
            warnings.append(ValidationWarningInfo(
                field_name=field_name,
                severity="info",
                message=f"Policy number '{s}' unusually short (< 4 chars)",
            ))
        return True, warnings

    if field_name == "premium_frequency":
        valid = {"monthly", "quarterly", "semi-annual", "annual", "single", "unknown"}
        if str(value).lower() not in valid:
            warnings.append(ValidationWarningInfo(
                field_name=field_name,
                severity="info",
                message=f"premium_frequency '{value}' not in standard enum",
            ))
        return True, warnings

    # default: accept non-empty
    if isinstance(value, (list, dict)) and not value:
        return False, warnings
    return True, warnings


def _is_grounded_in_source(field_name: str, value: Any, full_text: str, full_text_lower: str) -> bool:
    """Check if the extracted value appears in the source text (normalized
    forms for dates and money). Returns True if grounded."""
    if value is None or value == "" or value == [] or value == {}:
        return False

    if field_name in ("effective_date", "expiration_date"):
        return _date_in_text(value, full_text)

    if field_name in ("premium_amount", "face_amount"):
        return _money_in_text(value, full_text)

    if field_name in ("coverage_limits", "exclusions"):
        # Lists — ground each item
        if isinstance(value, list):
            return any(_string_in_text(str(item), full_text_lower) for item in value)
        return False

    return _string_in_text(str(value), full_text_lower)


def _string_in_text(needle: str, full_text_lower: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return False
    return needle in full_text_lower


def _date_in_text(value: Any, full_text: str) -> bool:
    """Try multiple date string representations against source."""
    try:
        if isinstance(value, date):
            d = value
        else:
            d = datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False

    candidates = [
        d.strftime("%Y-%m-%d"),
        d.strftime("%m/%d/%Y"),
        d.strftime("%m/%d/%y"),
        d.strftime("%B %-d, %Y"),     # "May 1, 2008"
        d.strftime("%B %d, %Y"),      # "May 01, 2008"
        d.strftime("%b %-d, %Y"),     # "May 1, 2008" (short month)
        d.strftime("%b %d, %Y"),
        d.strftime("%-d %B %Y"),
        d.strftime("%B %-d,%Y").upper(),  # "MAY 1,2008"
        d.strftime("%B %d, %Y").upper(),
        d.strftime("%B %-d, %Y").upper(),
    ]
    text_lower = full_text.lower()
    for c in candidates:
        if c.lower() in text_lower:
            return True
    return False


def _money_in_text(value: Any, full_text: str) -> bool:
    """Match money amounts allowing for $ prefix, commas, optional decimals."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return False

    # Build regex patterns: integer form, comma form, decimal form
    # 500000 -> "500,000" or "500000" or "$500,000" etc.
    integer = int(d)
    candidates = [
        f"{integer:,}",          # 500,000
        f"{integer}",            # 500000
        f"{integer:,}.00",       # 500,000.00
        f"{d:.2f}",              # 500000.00
        f"{integer:,}.0",
    ]
    # Also try the original Decimal string
    candidates.append(str(d))
    text = full_text
    for c in candidates:
        if c in text:
            return True
    return False


def _cross_field_checks(fields: dict[str, Any]) -> list[ValidationWarningInfo]:
    warnings: list[ValidationWarningInfo] = []

    eff = fields.get("effective_date")
    exp = fields.get("expiration_date")

    def to_date(v):
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    eff_d = to_date(eff)
    exp_d = to_date(exp)

    if eff_d and exp_d:
        if eff_d >= exp_d:
            warnings.append(ValidationWarningInfo(
                field_name=None,
                severity="error",
                message=f"effective_date ({eff_d}) is not before expiration_date ({exp_d})",
            ))
        elif (exp_d - eff_d).days < 30:
            warnings.append(ValidationWarningInfo(
                field_name=None,
                severity="info",
                message=f"Policy duration is unusually short: {(exp_d - eff_d).days} days",
            ))

    # face_amount vs premium_amount sanity for life-like policies
    try:
        premium = fields.get("premium_amount")
        face = fields.get("face_amount")
        freq = (fields.get("premium_frequency") or "").lower()
        if premium and face:
            p = Decimal(str(premium))
            f = Decimal(str(face))
            # rough: annual premium should be < 1/10th of face amount typically
            multiplier = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "annual": 1, "single": 1}.get(freq, 1)
            annual_premium = p * multiplier
            if annual_premium * 10 > f:
                warnings.append(ValidationWarningInfo(
                    field_name=None,
                    severity="info",
                    message=f"Annual premium (${annual_premium}) > 10% of face amount (${f}) — unusual ratio, please verify",
                ))
    except Exception:
        pass

    return warnings
