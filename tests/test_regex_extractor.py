"""Tests for app.extractors.regex — deterministic pre-pass extractor."""

from __future__ import annotations

import pytest

try:
    from app.extractors.regex import merge_with_llm, regex_extract
except ImportError:  # pragma: no cover
    pytest.skip("app.extractors.regex not built yet", allow_module_level=True)

try:
    from app.pdf import extract_full_text, extract_pages
except ImportError:  # pragma: no cover
    extract_full_text = None
    extract_pages = None


def test_regex_extract_sample_pdf_finds_policy_number(sample_pdf_path):
    if extract_pages is None or extract_full_text is None:
        pytest.skip("app.pdf not available")
    full_text = extract_full_text(extract_pages(sample_pdf_path))
    out = regex_extract(full_text)
    assert isinstance(out, dict)
    assert out.get("policy_number") == "VF99999990"


def test_regex_extract_labeled_policy_number():
    out = regex_extract("Policy Number: ABC-123456")
    assert out.get("policy_number") == "ABC-123456"


def test_regex_extract_returns_empty_dict_when_nothing_matches():
    out = regex_extract("no policy info here")
    assert out == {}


def test_regex_extract_no_pure_alpha_policy_numbers():
    # Pattern requires at least one digit + len >= 4 to filter noise.
    out = regex_extract("Policy Number: ABCDEFG")
    assert "policy_number" not in out


def test_merge_with_llm_regex_wins_for_overlapping_field():
    regex_fields = {"policy_number": "REGEX-X1"}
    llm_fields = {
        "policy_number": "LLM-X9",
        "insurer_name": "Acme",
    }
    merged, origin = merge_with_llm(regex_fields, llm_fields)
    assert merged["policy_number"] == "REGEX-X1"
    assert origin["policy_number"] == "regex"
    # Non-overlapping LLM field passes through.
    assert merged["insurer_name"] == "Acme"
    assert origin["insurer_name"] == "llm"


def test_merge_with_llm_fills_from_llm_when_regex_empty():
    merged, origin = merge_with_llm({}, {"insurer_name": "Foo Insurance"})
    assert merged["insurer_name"] == "Foo Insurance"
    assert origin["insurer_name"] == "llm"


def test_merge_with_llm_empty_inputs_produce_empty_outputs():
    merged, origin = merge_with_llm({}, {})
    assert merged == {}
    assert origin == {}
