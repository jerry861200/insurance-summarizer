"""Tests for app.pdf — page extraction, full-text concat, scanned-PDF heuristic."""

from __future__ import annotations

import pytest

try:
    from app.pdf import PageText, extract_full_text, extract_pages, is_likely_scanned
except ImportError:  # pragma: no cover
    pytest.skip("app.pdf not built yet", allow_module_level=True)


def test_extract_pages_returns_at_least_ten_pages(sample_pdf_path):
    pages = extract_pages(sample_pdf_path)
    assert len(pages) >= 10, f"Expected >=10 pages, got {len(pages)}"
    # Every page object is the right shape.
    for p in pages:
        assert isinstance(p, PageText)
        assert p.page_num >= 1
        assert p.char_count == len(p.text)


def test_is_likely_scanned_false_for_native_pdf(sample_pdf_path):
    pages = extract_pages(sample_pdf_path)
    assert is_likely_scanned(pages) is False


def test_extract_full_text_contains_known_values_and_markers(sample_pdf_path):
    pages = extract_pages(sample_pdf_path)
    full_text = extract_full_text(pages)
    assert "VF99999990" in full_text
    assert "LELAND STANFORD" in full_text
    assert "Pacific Life" in full_text
    assert "--- PAGE 1 ---" in full_text


def test_is_likely_scanned_true_for_synthetic_empty_pages():
    empty_pages = [PageText(page_num=i, text="", char_count=0) for i in range(1, 4)]
    assert is_likely_scanned(empty_pages) is True


def test_is_likely_scanned_false_for_empty_list():
    # Per docstring: zero pages -> False (treat as 'unknown -- try native first').
    assert is_likely_scanned([]) is False


def test_extract_pages_raises_on_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(FileNotFoundError):
        extract_pages(missing)
