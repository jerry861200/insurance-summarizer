from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class PageText:
    page_num: int  # 1-indexed
    text: str
    char_count: int


def extract_pages(pdf_path: str | Path) -> list[PageText]:
    """Open the PDF with pdfplumber and return per-page text.

    Uses extract_text(layout=True) to preserve table/multi-column structure.
    If extract_text returns None (e.g., page has no extractable text),
    coerce to empty string.

    Raises:
        FileNotFoundError if pdf_path does not exist
        ValueError if file is not a valid PDF
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        pdf = pdfplumber.open(path)
    except FileNotFoundError:
        raise
    except Exception as e:
        raise ValueError("Not a valid PDF") from e

    pages: list[PageText] = []
    with pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            raw = page.extract_text(layout=True)
            text = (raw or "").rstrip()
            pages.append(
                PageText(
                    page_num=idx,
                    text=text,
                    char_count=len(text),
                )
            )
    return pages


def extract_full_text(pages: list[PageText]) -> str:
    """Concatenate pages with `--- PAGE N ---` markers so the LLM
    can cite source page numbers.
    """
    parts: list[str] = []
    for page in pages:
        parts.append(f"--- PAGE {page.page_num} ---\n\n{page.text}")
    return "\n\n".join(parts)


def is_likely_scanned(pages: list[PageText]) -> bool:
    """Heuristic: avg chars per page < 100 suggests the PDF is scanned
    (no embedded text) and we should fall back to OCR.

    Returns False for zero pages (treat as 'unknown -- try native first').
    """
    if not pages:
        return False
    avg = sum(p.char_count for p in pages) / max(1, len(pages))
    return avg < 100


def page_count(pages: list[PageText]) -> int:
    """Convenience: return number of pages."""
    return len(pages)


def total_char_count(pages: list[PageText]) -> int:
    """Convenience: total chars across all pages."""
    return sum(p.char_count for p in pages)
