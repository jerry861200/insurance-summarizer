from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import dateparser


# Patterns
POLICY_NUMBER_PATTERNS = [
    re.compile(
        r"Policy\s*(?:Number|No\.?|#)\s*[:\-]?\s*([A-Z0-9\-]{4,32})",
        re.IGNORECASE,
    ),
    re.compile(r"Policy\s*ID\s*[:\-]?\s*([A-Z0-9\-]{4,32})", re.IGNORECASE),
    re.compile(r"Contract\s*Number\s*[:\-]?\s*([A-Z0-9\-]{4,32})", re.IGNORECASE),
]

DATE_LABEL_PATTERNS = {
    "effective_date": [
        re.compile(
            r"(?:Policy|Effective|Inception)\s*Date\s*[:\-]?\s*([A-Za-z0-9,\s\-/]{6,30})",
            re.IGNORECASE,
        ),
    ],
    "expiration_date": [
        re.compile(
            r"(?:Expiration|Expiry|Maturity|Coverage\s*End)\s*Date\s*[:\-]?\s*([A-Za-z0-9,\s\-/]{6,30})",
            re.IGNORECASE,
        ),
    ],
}

MONEY_PATTERNS = {
    "face_amount": [
        re.compile(
            r"Face\s*Amount\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"Death\s*Benefit\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"Sum\s*Insured\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            re.IGNORECASE,
        ),
    ],
    "premium_amount": [
        re.compile(
            r"(?:Initial\s*|Monthly\s*|Annual\s*)?Premium\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            re.IGNORECASE,
        ),
    ],
}


def regex_extract(full_text: str) -> dict[str, Any]:
    """Best-effort regex extraction. Returns dict of fields it could extract
    with high confidence. Missing fields are simply absent from the dict
    (NOT None) so the caller can distinguish "regex didn't try" from "regex
    tried and found nothing".

    Output keys (any subset may be present):
        policy_number: str
        effective_date: str (ISO 8601 YYYY-MM-DD)
        expiration_date: str (ISO 8601)
        premium_amount: float
        face_amount: float
    """
    out: dict[str, Any] = {}

    for pat in POLICY_NUMBER_PATTERNS:
        m = pat.search(full_text)
        if m:
            candidate = m.group(1).strip()
            # Filter out obvious noise (e.g., must contain at least one digit)
            if any(c.isdigit() for c in candidate) and len(candidate) >= 4:
                out["policy_number"] = candidate
                break

    for field, patterns in DATE_LABEL_PATTERNS.items():
        for pat in patterns:
            m = pat.search(full_text)
            if m:
                raw = m.group(1).strip().rstrip(":-").strip()
                parsed = dateparser.parse(raw, settings={"STRICT_PARSING": False})
                if parsed:
                    out[field] = parsed.date().isoformat()
                    break

    for field, patterns in MONEY_PATTERNS.items():
        for pat in patterns:
            m = pat.search(full_text)
            if m:
                raw = m.group(1).replace(",", "")
                try:
                    val = float(Decimal(raw))
                    if 0 < val < 1e9:
                        out[field] = val
                        break
                except Exception:
                    continue

    return out


def merge_with_llm(
    regex_fields: dict, llm_fields: dict
) -> tuple[dict, dict[str, str]]:
    """Merge regex output and LLM output. Returns (merged_fields, origin_per_field).

    Strategy: regex wins when both have a value (regex is deterministic, less
    likely to hallucinate). LLM fills the gaps and provides fields regex doesn't
    handle (insurer_name, exclusions, coverage_limits, etc.).
    """
    merged = dict(llm_fields)
    origin = {
        k: "llm" for k, v in llm_fields.items() if v not in (None, [], {}, "")
    }

    for k, v in regex_fields.items():
        if v is not None:
            merged[k] = v
            origin[k] = "regex"

    return merged, origin
