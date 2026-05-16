from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass
class FieldResult:
    field_name: str
    expected: Any
    actual: Any
    passed: bool
    score: float           # 0.0 to 1.0 (partial credit for substring matches, near-dates, etc.)
    note: str = ""         # explanation


@dataclass
class CaseResult:
    case_name: str
    field_results: list[FieldResult]
    overall_score: float   # mean of field scores
    overall_passed: bool   # all required fields passed

    @property
    def passed_count(self) -> int:
        return sum(1 for fr in self.field_results if fr.passed)

    @property
    def total_count(self) -> int:
        return len(self.field_results)


def compare_string(field: str, expected, actual, case_insensitive: bool = True) -> FieldResult:
    """Exact string compare with optional case-insensitive."""
    if actual is None or expected is None:
        passed = expected is None and actual is None
        return FieldResult(field, expected, actual, passed, 1.0 if passed else 0.0,
                           note=("both null" if passed else "value missing"))
    e = str(expected).strip()
    a = str(actual).strip()
    if case_insensitive:
        e, a = e.lower(), a.lower()
    passed = e == a
    if passed:
        return FieldResult(field, expected, actual, True, 1.0, "exact match")
    # Partial credit if one is substring of the other
    if e in a or a in e:
        return FieldResult(field, expected, actual, False, 0.5, "partial match (substring)")
    return FieldResult(field, expected, actual, False, 0.0, "mismatch")


def compare_date(field: str, expected, actual, tolerance_days: int = 1) -> FieldResult:
    """Date compare with tolerance."""
    if actual is None or expected is None:
        passed = expected is None and actual is None
        return FieldResult(field, expected, actual, passed, 1.0 if passed else 0.0,
                           note=("both null" if passed else "value missing"))
    def to_date(v):
        if isinstance(v, date):
            return v
        try:
            return datetime.strptime(str(v), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    e = to_date(expected)
    a = to_date(actual)
    if e is None or a is None:
        return FieldResult(field, expected, actual, False, 0.0, "could not parse date")
    delta = abs((a - e).days)
    if delta == 0:
        return FieldResult(field, expected, actual, True, 1.0, "exact match")
    if delta <= tolerance_days:
        return FieldResult(field, expected, actual, True, 0.9, f"within +/-{tolerance_days}d ({delta}d off)")
    return FieldResult(field, expected, actual, False, 0.0, f"off by {delta}d")


def compare_money(field: str, expected, actual, tolerance: Decimal = Decimal("0.01")) -> FieldResult:
    if actual is None or expected is None:
        passed = expected is None and actual is None
        return FieldResult(field, expected, actual, passed, 1.0 if passed else 0.0,
                           note=("both null" if passed else "value missing"))
    try:
        e = Decimal(str(expected))
        a = Decimal(str(actual))
    except Exception:
        return FieldResult(field, expected, actual, False, 0.0, "could not parse money")
    delta = abs(e - a)
    if delta <= tolerance:
        return FieldResult(field, expected, actual, True, 1.0, "exact within tolerance")
    rel_err = float(delta / e) if e else float("inf")
    if rel_err < 0.05:
        return FieldResult(field, expected, actual, False, 0.5, f"5% close (off by ${delta})")
    return FieldResult(field, expected, actual, False, 0.0, f"off by ${delta}")


def compare_substrings(field: str, must_include: list[str], actual_list: list[str]) -> FieldResult:
    """For exclusions: check that ALL expected substrings appear in some entry of actual_list."""
    if not isinstance(actual_list, list):
        return FieldResult(field, must_include, actual_list, False, 0.0, "not a list")
    actual_text = " ".join(actual_list).lower()
    missing = [s for s in must_include if s.lower() not in actual_text]
    if not missing:
        return FieldResult(field, must_include, actual_list, True, 1.0,
                           f"all {len(must_include)} substrings found")
    return FieldResult(field, must_include, actual_list, False,
                       (len(must_include) - len(missing)) / len(must_include),
                       f"missing: {missing}")


def evaluate_case(expected: dict, actual: dict, case_name: str) -> CaseResult:
    """Compare an actual extraction against the expected ground truth.
    Returns CaseResult with per-field FieldResult and overall stats."""
    results: list[FieldResult] = []

    string_fields = ["policy_number", "insured_name", "insurer_name", "policy_type", "premium_frequency", "premium_currency"]
    for f in string_fields:
        if f in expected:
            results.append(compare_string(f, expected[f], actual.get(f)))

    date_fields = ["effective_date", "expiration_date"]
    for f in date_fields:
        if f in expected:
            results.append(compare_date(f, expected[f], actual.get(f)))

    money_fields = ["premium_amount", "face_amount"]
    for f in money_fields:
        if f in expected:
            results.append(compare_money(f, expected[f], actual.get(f)))

    if "exclusions_must_include_substrings" in expected:
        results.append(compare_substrings(
            "exclusions",
            expected["exclusions_must_include_substrings"],
            actual.get("exclusions") or [],
        ))

    overall_score = sum(r.score for r in results) / len(results) if results else 0.0
    overall_passed = all(r.passed for r in results)

    return CaseResult(
        case_name=case_name,
        field_results=results,
        overall_score=overall_score,
        overall_passed=overall_passed,
    )


def render_report(case_results: list[CaseResult]) -> str:
    """Render Markdown report. Returns the markdown string."""
    lines = ["# Evaluation Report", ""]
    n_passed_cases = sum(1 for cr in case_results if cr.overall_passed)
    avg_score = sum(cr.overall_score for cr in case_results) / len(case_results) if case_results else 0.0
    lines.append(f"**Cases:** {len(case_results)} | **Fully passed:** {n_passed_cases} | **Average score:** {avg_score:.2%}")
    lines.append("")

    for cr in case_results:
        lines.append(f"## {cr.case_name} - {cr.passed_count}/{cr.total_count} fields passed, score {cr.overall_score:.2%}")
        lines.append("")
        lines.append("| Field | Expected | Actual | Score | Note |")
        lines.append("|---|---|---|---|---|")
        for fr in cr.field_results:
            mark = "PASS" if fr.passed else "FAIL"
            exp = str(fr.expected)[:40]
            act = str(fr.actual)[:40] if fr.actual is not None else "-"
            lines.append(f"| {mark} {fr.field_name} | `{exp}` | `{act}` | {fr.score:.0%} | {fr.note} |")
        lines.append("")
    return "\n".join(lines)
