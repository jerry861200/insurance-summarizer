"""Generate synthetic insurance policy PDFs for the eval harness.

Usage:
    python -m eval.generate_synthetic_pdfs

Produces:
    eval/golden/short_term_policy.pdf              - clean 2-page life policy with all standard fields
    eval/golden/missing_fields_policy.pdf          - 1-page policy with intentional missing fields
                                                     (tests null-over-guess extraction behavior)
    eval/golden/commercial_general_liability.pdf   - commercial GL policy with $1M/$2M occurrence
                                                     /aggregate limits and pollution/professional
                                                     exclusions; uses "Named Insured" and
                                                     "Policy Period" labels (not life-insurance style)
    eval/golden/auto_multi_vehicle.pdf             - personal auto policy covering two vehicles, each
                                                     with BI/PD/Comp/Collision limits; semi-annual
                                                     premium format
    eval/golden/homeowners_ho3.pdf                 - HO-3 form with dwelling/personal-property
                                                     /liability coverages and standard exclusions

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


def make_commercial_general_liability() -> Path:
    """Commercial General Liability policy. Different format conventions than
    life insurance: 'Named Insured' label, combined 'Policy Period: A - B' line
    instead of separate effective/expiration, per-occurrence/aggregate dollar
    limits, professional/pollution exclusions."""
    path = GOLDEN_DIR / "commercial_general_liability.pdf"
    c = canvas.Canvas(str(path), pagesize=LETTER)

    lines = [
        ("ATLANTIC COMMERCIAL RISK GROUP", 14),
        ("450 Harbor Boulevard, Boston, MA 02110", 10),
        ("", 10),
        ("COMMERCIAL GENERAL LIABILITY POLICY - DECLARATIONS", 12),
        ("", 10),
        ("Policy Number: CGL-2024-554433", 11),
        ("Named Insured: Riverside Manufacturing LLC", 11),
        ("Business Address: 88 Industrial Way, Quincy, MA 02169", 10),
        ("Form of Business: Limited Liability Company", 10),
        ("Policy Type: Commercial General Liability (Occurrence)", 11),
        ("", 10),
        ("Policy Period: 01/01/2024 - 01/01/2025 (12:01 A.M. Standard Time)", 11),
        ("", 10),
        ("PREMIUM", 12),
        ("Annual Premium: $5,400.00", 11),
        ("Payment Frequency: Annual", 11),
        ("Currency: USD", 11),
        ("", 10),
        ("LIMITS OF INSURANCE", 12),
        ("Each Occurrence Limit:                 $1,000,000", 10),
        ("General Aggregate Limit:               $2,000,000", 10),
        ("Products/Completed Operations Agg:     $2,000,000", 10),
        ("Personal & Advertising Injury:         $1,000,000", 10),
        ("Damage to Premises Rented:             $   100,000", 10),
        ("Medical Expense (any one person):      $    10,000", 10),
        ("", 10),
        ("EXCLUSIONS - this policy does NOT cover:", 12),
        ("- Pollution and environmental contamination of any kind, including", 10),
        ("  bodily injury or property damage arising from the discharge,", 10),
        ("  dispersal, seepage, migration, release or escape of pollutants.", 10),
        ("- Professional liability or errors and omissions arising out of the", 10),
        ("  rendering of or failure to render any professional services.", 10),
        ("- Bodily injury to an employee of the insured arising out of and in", 10),
        ("  the course of employment by the insured (employee actions are", 10),
        ("  covered by workers compensation, not this policy).", 10),
        ("- Expected or intended injury caused by the insured.", 10),
        ("- Liability assumed under a contract not falling within the", 10),
        ("  definition of an insured contract.", 10),
    ]
    _write_lines(c, lines)
    c.save()
    return path


def make_auto_multi_vehicle() -> Path:
    """Personal auto policy covering two vehicles. Tests:
    - non-life premium frequency (semi-annual)
    - multi-row coverage_limits structure (per-vehicle BI/PD/Comp/Collision)
    - non-life exclusions (racing, uninsured drivers, intentional acts)
    """
    path = GOLDEN_DIR / "auto_multi_vehicle.pdf"
    c = canvas.Canvas(str(path), pagesize=LETTER)

    lines = [
        ("MIDWEST AUTO & PROPERTY INSURANCE", 14),
        ("220 Lakeshore Drive, Chicago, IL 60601", 10),
        ("", 10),
        ("PERSONAL AUTO POLICY - DECLARATIONS PAGE", 12),
        ("", 10),
        ("Policy Number: PA-2024-887766", 11),
        ("Named Insured: Maria Gonzalez", 11),
        ("Address: 147 Oak Street, Naperville, IL 60540", 10),
        ("Policy Type: Personal Auto", 11),
        ("", 10),
        ("Policy Effective Date: June 1, 2024", 11),
        ("Policy Expiration Date: December 1, 2024", 11),
        ("Premium: $920.00 (Semi-Annual)", 11),
        ("Payment Frequency: Semi-Annual", 11),
        ("Currency: USD", 11),
        ("", 10),
        ("COVERED VEHICLES", 12),
        ("", 10),
        ("Vehicle 1: 2020 Honda Civic LX  -  VIN 19XFC2F59LE000111", 11),
        ("  Bodily Injury Liability:        $100,000 / $300,000", 10),
        ("  Property Damage Liability:      $ 50,000", 10),
        ("  Comprehensive (Other Than Coll): $500 deductible / $25,000 limit", 10),
        ("  Collision:                       $500 deductible / $25,000 limit", 10),
        ("", 10),
        ("Vehicle 2: 2018 Toyota Camry SE  -  VIN 4T1B11HK5JU000222", 11),
        ("  Bodily Injury Liability:        $100,000 / $300,000", 10),
        ("  Property Damage Liability:      $ 50,000", 10),
        ("  Comprehensive (Other Than Coll): $500 deductible / $20,000 limit", 10),
        ("  Collision:                       $500 deductible / $20,000 limit", 10),
        ("", 10),
        ("EXCLUSIONS - this policy does NOT cover:", 12),
        ("- Damage or injury resulting from racing, speed contests, or any", 10),
        ("  organized track-event use of a covered vehicle.", 10),
        ("- Loss caused by a driver who is not listed on the policy and does", 10),
        ("  not have your permission (uninsured drivers operating your vehicle).", 10),
        ("- Damage or injury that is intentional acts of the insured or any", 10),
        ("  household resident.", 10),
        ("- Use of the vehicle as a public or livery conveyance (commercial", 10),
        ("  ride-share/taxi service without commercial endorsement).", 10),
        ("- Loss while the vehicle is being used in the course of a felony.", 10),
    ]
    _write_lines(c, lines)
    c.save()
    return path


def make_homeowners_ho3() -> Path:
    """Homeowners HO-3 form. Tests property-insurance vocabulary:
    'Dwelling Coverage', 'Personal Property', 'Personal Liability' instead of
    life-insurance face amount; flood/earthquake/war/wear-and-tear exclusions."""
    path = GOLDEN_DIR / "homeowners_ho3.pdf"
    c = canvas.Canvas(str(path), pagesize=LETTER)

    lines = [
        ("BEACON MUTUAL HOMEOWNERS", 14),
        ("300 Lighthouse Avenue, Providence, RI 02903", 10),
        ("", 10),
        ("HOMEOWNERS POLICY - HO-3 SPECIAL FORM", 12),
        ("DECLARATIONS PAGE", 12),
        ("", 10),
        ("Policy Number: HO3-456789-2024", 11),
        ("Named Insured: Robert Chen", 11),
        ("Insured Location: 24 Maple Lane, Cranston, RI 02920", 10),
        ("Policy Type: Homeowners HO-3", 11),
        ("", 10),
        ("Policy Effective Date: April 15, 2024", 11),
        ("Policy Expiration Date: April 15, 2025", 11),
        ("Annual Premium: $1,250.00", 11),
        ("Payment Frequency: Annual", 11),
        ("Currency: USD", 11),
        ("", 10),
        ("COVERAGES", 12),
        ("Coverage A - Dwelling:           $400,000", 10),
        ("Coverage B - Other Structures:   $ 40,000", 10),
        ("Coverage C - Personal Property:  $200,000", 10),
        ("Coverage D - Loss of Use:        $ 80,000", 10),
        ("Coverage E - Personal Liability: $300,000 (each occurrence)", 10),
        ("Coverage F - Medical Payments:   $  5,000 (each person)", 10),
        ("Deductible (All Perils):         $  1,000", 10),
        ("", 10),
        ("EXCLUSIONS - this policy does NOT cover loss caused by:", 12),
        ("- Flood, surface water, waves, tidal water, overflow of any body", 10),
        ("  of water, or spray from any of these (separate flood insurance", 10),
        ("  is required).", 10),
        ("- Earthquake, earth movement, mudslide, mine subsidence, sinkhole", 10),
        ("  collapse, or any other earth movement (whether or not combined", 10),
        ("  with water).", 10),
        ("- War, undeclared war, civil war, insurrection, rebellion, or", 10),
        ("  revolution, including any consequence of these.", 10),
        ("- Wear and tear, marring, deterioration, inherent vice, latent", 10),
        ("  defect, or mechanical breakdown.", 10),
        ("- Nuclear hazard, including any nuclear reaction, radiation, or", 10),
        ("  radioactive contamination.", 10),
        ("- Intentional loss caused by an insured.", 10),
    ]
    _write_lines(c, lines)
    c.save()
    return path


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    generated = [
        make_short_term_policy(),
        make_missing_fields_policy(),
        make_commercial_general_liability(),
        make_auto_multi_vehicle(),
        make_homeowners_ho3(),
    ]
    for p in generated:
        print(f"Generated: {p.name}")


if __name__ == "__main__":
    main()
