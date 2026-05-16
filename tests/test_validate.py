"""Tests for app.validate — per-field confidence + cross-field warnings."""

from __future__ import annotations

import pytest

try:
    from app.validate import validate
except ImportError:  # pragma: no cover
    pytest.skip("app.validate not built yet", allow_module_level=True)

try:
    from app.pdf import extract_full_text, extract_pages
except ImportError:  # pragma: no cover
    extract_full_text = None
    extract_pages = None


# Known-good Leland Stanford expected values.
GOOD_FIELDS = {
    "policy_number": "VF99999990",
    "insured_name": "LELAND STANFORD",
    "insurer_name": "Pacific Life Insurance Company",
    "policy_type": "Term Life",
    "effective_date": "2008-05-01",
    "expiration_date": "2068-05-01",
    "premium_amount": 36.0,
    "premium_frequency": "monthly",
    "face_amount": 500000.0,
    "coverage_limits": [
        {"name": "Face Amount", "limit_amount": 500000.0, "currency": "USD"}
    ],
    "exclusions": ["Suicide within 2 years of policy date"],
}


@pytest.fixture
def sample_full_text(sample_pdf_path):
    if extract_pages is None or extract_full_text is None:
        pytest.skip("app.pdf not available")
    return extract_full_text(extract_pages(sample_pdf_path))


def test_happy_path_per_field_confidence(sample_full_text):
    result = validate(
        GOOD_FIELDS,
        sample_full_text,
        source_page_hints={"policy_number": [1], "insured_name": [1]},
    )
    assert result.confidence["policy_number"] >= 0.85
    assert result.confidence["insured_name"] >= 0.80


def test_happy_path_no_error_warnings(sample_full_text):
    result = validate(
        GOOD_FIELDS,
        sample_full_text,
        source_page_hints={"policy_number": [1], "insured_name": [1]},
    )
    error_warnings = [w for w in result.warnings if w.severity == "error"]
    assert error_warnings == [], (
        f"Unexpected error-level warnings on happy path: "
        f"{[(w.field_name, w.message) for w in error_warnings]}"
    )


def test_hallucinated_policy_number_flagged(sample_full_text):
    bad_fields = dict(GOOD_FIELDS)
    bad_fields["policy_number"] = "FAKE99999"
    result = validate(bad_fields, sample_full_text)
    assert result.confidence["policy_number"] < 0.7
    msgs = " ".join(w.message.lower() for w in result.warnings)
    assert "hallucination" in msgs


def test_cross_field_effective_after_expiration_is_error():
    fields = dict(GOOD_FIELDS)
    fields["effective_date"] = "2070-01-01"  # after expiration_date
    fields["expiration_date"] = "2068-05-01"
    # Empty source text is fine for the cross-field check.
    result = validate(fields, full_text="")
    cross = [
        w for w in result.warnings
        if w.severity == "error" and (w.field_name is None or "date" in (w.field_name or ""))
    ]
    assert cross, f"Expected cross-field error warning, got {result.warnings}"
    assert any("effective" in w.message.lower() for w in cross)


def test_missing_policy_number_produces_error_warning(sample_full_text):
    fields = dict(GOOD_FIELDS)
    fields["policy_number"] = None
    result = validate(fields, sample_full_text)
    error_warnings = [
        w for w in result.warnings
        if w.severity == "error" and w.field_name == "policy_number"
    ]
    assert error_warnings, (
        f"Expected an error warning about missing policy_number, "
        f"got {[(w.severity, w.field_name, w.message) for w in result.warnings]}"
    )


def test_completely_empty_fields_returns_warnings(sample_full_text):
    # Should at least flag missing critical fields.
    result = validate({}, sample_full_text)
    severities = {w.severity for w in result.warnings}
    assert "error" in severities
