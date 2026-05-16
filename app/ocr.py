from __future__ import annotations

import shutil
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path

from app.pdf import PageText  # reuse the dataclass for type consistency


# Constants
DEFAULT_DPI = 200  # higher = better OCR, slower
DEFAULT_LANG = "eng"

# Auto-locate tesseract: try PATH first, then common Homebrew/Linux paths.
# Solves environments (e.g. sandboxed shells) where /opt/homebrew/bin isn't on PATH.
_TESSERACT_CANDIDATES = [
    "/opt/homebrew/bin/tesseract",  # macOS Apple Silicon (Homebrew)
    "/usr/local/bin/tesseract",     # macOS Intel + many Linux
    "/usr/bin/tesseract",           # Debian/Ubuntu/RHEL
]
_found_tesseract = shutil.which("tesseract") or next(
    (p for p in _TESSERACT_CANDIDATES if Path(p).exists()), None
)
if _found_tesseract:
    pytesseract.pytesseract.tesseract_cmd = _found_tesseract


def ocr_pdf(
    pdf_path: str | Path,
    dpi: int = DEFAULT_DPI,
    lang: str = DEFAULT_LANG,
) -> list[PageText]:
    """OCR every page of the PDF and return list[PageText] in the same shape
    as app.pdf.extract_pages() — so the pipeline can swap implementations
    without changing downstream code.

    Implementation:
        1. Rasterize each page via pdf2image.convert_from_path (uses poppler)
        2. Run pytesseract.image_to_string on each PIL Image
        3. Wrap in PageText(page_num=1-indexed, text=..., char_count=...)

    Raises:
        FileNotFoundError if pdf_path missing
        pytesseract.TesseractNotFoundError if tesseract binary not installed
        (let these propagate — caller decides how to handle)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    images = convert_from_path(str(pdf_path), dpi=dpi)
    pages: list[PageText] = []
    for i, image in enumerate(images, start=1):
        text = (pytesseract.image_to_string(image, lang=lang) or "").rstrip()
        pages.append(PageText(page_num=i, text=text, char_count=len(text)))
    return pages


def is_tesseract_available() -> bool:
    """Quick check — returns True if tesseract binary is installed.
    Useful for /healthz to report OCR capability."""
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False
