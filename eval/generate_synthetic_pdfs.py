"""Generate synthetic insurance policy PDFs for the eval harness.

Usage:
    python -m eval.generate_synthetic_pdfs

Produces:
    eval/golden/short_term_policy.pdf       - clean 2-page policy with all standard fields
    eval/golden/missing_fields_policy.pdf   - 1-page policy with intentional missing fields
                                              (tests null-over-guess extraction behavior)

Run after editing if you tweak the content - these PDFs are checked into git as
test fixtures, not generated at test time.
"""
from __future__ import annotations
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

GOLDEN_DIR = Path(__file__).parent / "golden"


def _write_lines(c: canvas.Canvas, lines: list[tuple[str, int]]) -> None:
    """Helper: write a list of (text, font_size) tuples top-down with sensible spacing.

    Wraps to a new page if we hit the bottom margin.
    """
    y = 10.5 * inch
    margin_bottom = 1.0 * inch
    for text, size in lines:
        if y < margin_bottom:
            c.showPage()
            y = 10.5 * inch
        c.setFont("Helvetica" if size <= 11 else "Helvetica-Bold", size)
        c.drawString(1.0 * inch, y, text)
        y -= (size + 6)


def make_short_term_policy() -> Path:
    """Clean 2-page synthetic policy. Different formatting from the Pacific Life
    Leland Stanford sample (uses 'Policy No.', explicit annual premium, etc.)
    to exercise format variety."""
    path = GOLDEN_DIR / "short_term_policy.pdf"
    c = canvas.Canvas(str(path), pagesize=LETTER)

    lines = [
        ("CONSOLIDATED MUTUAL LIFE INSURANCE COMPANY", 14),
        ("12 Market Street, Springfield, IL 62701", 10),
        ("", 10),
        ("CERTIFICATE OF INSURANCE - TERM LIFE POLICY", 13),
        ("", 10),
        ("Policy No.: ABC-456789", 11),
        ("Insured: Jane Doe", 11),
        ("Coverage Type: Term Life Insurance", 11),
        ("Risk Class: Preferred Non-Smoker", 11),
        ("", 10),
        ("Policy Effective Date: January 15, 2023", 11),
        ("Policy Expiration Date: January 15, 2043", 11),
        ("", 10),
        ("Face Amount: $250,000.00", 11),
        ("Annual Premium: $480.00", 11),
        ("Payment Frequency: Annual", 11),
        ("", 10),
        ("DEATH BENEFIT", 12),
        ("The policy provides a death benefit of $250,000 payable upon the death", 10),
        ("of the insured during the policy term, subject to the exclusions below.", 10),
        ("", 10),
        ("EXCLUSIONS", 12),
        ("- Suicide within 2 years of the effective date: benefit limited to", 10),
        ("  the sum of premiums paid.", 10),
        ("- Material misrepresentation in the application during the first 2", 10),
        ("  years: policy may be voided.", 10),
        ("- Death resulting from participation in a war or armed conflict: no", 10),
        ("  benefit payable.", 10),
    ]
    _write_lines(c, lines)
    c.showPage()

    page2 = [
        ("PREMIUM SCHEDULE", 12),
        ("Years 1-5: $480.00 annual", 10),
        ("Years 6-10: $520.00 annual", 10),
        ("Years 11-15: $620.00 annual", 10),
        ("Years 16-20: $780.00 annual", 10),
        ("", 10),
        ("CONDITIONS", 12),
        ("Grace Period: 31 days after each premium due date", 10),
        ("Free Look Period: 10 days from delivery", 10),
        ("Conversion Option: convertible to permanent insurance through year 10", 10),
    ]
    _write_lines(c, page2)
    c.save()
    return path


def make_missing_fields_policy() -> Path:
    """1-page synthetic policy with intentionally missing fields. The extracted
    expiration_date and exclusions should come back null/empty - tests the
    'null over guess' extraction behavior."""
    path = GOLDEN_DIR / "missing_fields_policy.pdf"
    c = canvas.Canvas(str(path), pagesize=LETTER)

    lines = [
        ("ACME INSURANCE SERVICES, INC.", 14),
        ("", 10),
        ("BINDER OF INSURANCE - PRELIMINARY", 12),
        ("", 10),
        ("Policy Number: PB-202401", 11),
        ("Named Insured: John Smith", 11),
        ("Policy Type: Whole Life", 11),
        ("", 10),
        ("Effective Date: March 1, 2024", 11),
        ("Initial Monthly Premium: $125.50", 11),
        ("Death Benefit Amount: $100,000", 11),
        ("", 10),
        ("Note: This is a preliminary binder. Final policy details (including", 10),
        ("expiration / maturity date and applicable exclusions) will be issued", 10),
        ("upon underwriting completion. Refer to the issued policy for full terms.", 10),
    ]
    _write_lines(c, lines)
    c.save()
    return path


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    p1 = make_short_term_policy()
    p2 = make_missing_fields_policy()
    print(f"Generated: {p1.name}")
    print(f"Generated: {p2.name}")


if __name__ == "__main__":
    main()
