from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.extractors.llm import ExtractionResult, LLMProvider
from app.extractors.llm.base import SUMMARY_PROMPT_VERSION
from app.extractors.regex import merge_with_llm, regex_extract
from app.models import (
    Document,
    ExtractedFields,
    ProcessingRun,
    Summary,
    ValidationWarning,
)
from app.pdf import extract_full_text, extract_pages, is_likely_scanned
from app.storage import save_pdf_bytes

# Optional dependencies built by sibling agents. Use try/except so partial
# state still works while C2/C3 are landing.
try:
    from app.ocr import ocr_pdf  # type: ignore

    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False

try:
    from app.validate import validate as run_validation  # type: ignore

    _VALIDATE_AVAILABLE = True
except ImportError:
    _VALIDATE_AVAILABLE = False

try:
    from app.extractors.llm.verifier import verify_extraction  # type: ignore

    _VERIFIER_AVAILABLE = True
except ImportError:
    _VERIFIER_AVAILABLE = False


def process(
    file_bytes: bytes,
    filename: str,
    db: Session,
    llm: LLMProvider,
) -> Document:
    """Run the core pipeline on uploaded PDF bytes.

    Phase C flow:
      1. Validate + persist PDF bytes (Document row)
      2. Extract pages (with OCR fallback for scanned PDFs)
      3. Regex pre-pass for cheap deterministic fields
      4. LLM extraction for everything else
      5. Merge regex + LLM (regex wins on overlap)
      6. Validate the merged fields against the source text
      7. Persist ProcessingRun + ExtractedFields + ValidationWarnings
      8. Generate summary (failure here is non-fatal)
    """
    settings = get_settings()

    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400, detail="Not a valid PDF (missing %PDF header)"
        )
    if len(file_bytes) > settings.max_pdf_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds {settings.max_pdf_mb} MB limit",
        )

    document_id, pdf_path = save_pdf_bytes(file_bytes, filename)
    sha256 = hashlib.sha256(file_bytes).hexdigest()

    doc = Document(
        id=document_id,
        filename=filename,
        pdf_path=pdf_path,
        pdf_sha256=sha256,
        file_size_bytes=len(file_bytes),
        status="processing",
    )
    db.add(doc)
    db.flush()

    try:
        pages = extract_pages(pdf_path)
        if not pages:
            raise HTTPException(
                status_code=422, detail="PDF has no extractable pages"
            )

        # OCR fallback for scanned PDFs (when available)
        if is_likely_scanned(pages):
            if _OCR_AVAILABLE:
                try:
                    pages = ocr_pdf(pdf_path)
                    doc.extraction_method = "ocr"
                except Exception:
                    # If OCR itself fails, fall back to whatever native gave us
                    doc.extraction_method = "scanned-unhandled"
            else:
                doc.extraction_method = "scanned-unhandled"
        else:
            doc.extraction_method = "native"

        doc.page_count = len(pages)

        full_text = extract_full_text(pages)

        # 1. Cheap deterministic regex pre-pass
        regex_fields = regex_extract(full_text)

        # 2. LLM extraction
        started = datetime.now(timezone.utc)
        result: ExtractionResult = llm.extract_fields(full_text)
        completed = datetime.now(timezone.utc)

        # 2b. Optional cross-model verification (opt-in via CROSS_MODEL_VERIFY=true).
        # Runs the OTHER provider on the same text and surfaces scalar-field
        # disagreements as warning-severity ValidationWarnings later. Failure
        # is non-fatal: we emit an info-level warning and continue.
        cross_model_warnings: list = []
        if (
            _VERIFIER_AVAILABLE
            and settings.cross_model_verify
            and settings.anthropic_api_key
            and settings.openai_api_key
        ):
            try:
                from app.extractors.llm import get_llm_provider

                # Construct the OTHER provider via a mutated Settings copy.
                secondary_provider_name = (
                    "openai"
                    if settings.llm_provider == "anthropic"
                    else "anthropic"
                )
                secondary_settings = settings.model_copy(
                    update={"llm_provider": secondary_provider_name}
                )
                secondary = get_llm_provider(secondary_settings)

                disagreements = verify_extraction(result, full_text, secondary)
                for d in disagreements:
                    cross_model_warnings.append(
                        _SimpleWarning(
                            field_name=d.field_name,
                            severity="warning",
                            message=(
                                f"Cross-model disagreement: "
                                f"primary={d.primary_value!r}, "
                                f"secondary={d.secondary_value!r}"
                            ),
                        )
                    )
            except Exception as e:
                cross_model_warnings.append(
                    _SimpleWarning(
                        field_name=None,
                        severity="info",
                        message=(
                            f"Cross-model verification skipped: "
                            f"{type(e).__name__}: {e}"
                        ),
                    )
                )

        # 3. Merge — regex wins on overlap
        merged_fields, origin_per_field = merge_with_llm(
            regex_fields, result.fields
        )

        # 4. Validate
        validation_warnings: list = []
        confidence: dict[str, float] = {}
        if _VALIDATE_AVAILABLE:
            try:
                validation = run_validation(
                    merged_fields,
                    full_text,
                    result.source_page_hints,
                    origin_per_field,
                )
                validation_warnings = list(validation.warnings)
                confidence = dict(validation.confidence)
            except Exception as e:
                validation_warnings = []
                confidence = {}
                # Surface the validation failure as an info-level warning later
                validation_warnings.append(
                    _SimpleWarning(
                        field_name=None,
                        severity="info",
                        message=f"Validation skipped: {type(e).__name__}: {e}",
                    )
                )

        regex_fields_filled = sum(
            1 for v in regex_fields.values() if v not in (None, [], {}, "")
        )
        llm_fields_filled = sum(
            1
            for v in result.fields.values()
            if v not in (None, [], {}, "")
        )

        run = ProcessingRun(
            document_id=doc.id,
            run_number=1,
            extraction_prompt_version=result.prompt_version,
            summary_prompt_version=SUMMARY_PROMPT_VERSION,
            model_id=result.model_id,
            extraction_tokens_in=result.tokens_in,
            extraction_tokens_out=result.tokens_out,
            regex_fields_filled=regex_fields_filled,
            llm_fields_filled=llm_fields_filled,
            started_at=started,
            completed_at=completed,
            status="success",
        )
        db.add(run)
        db.flush()

        ef = _build_extracted_fields_row(
            run.id,
            merged_fields,
            result.source_page_hints,
            result.extraction_notes,
            confidence,
            origin_per_field,
        )
        db.add(ef)

        # Persist validation warnings
        for w in validation_warnings:
            db.add(
                ValidationWarning(
                    processing_run_id=run.id,
                    field_name=getattr(w, "field_name", None),
                    severity=getattr(w, "severity", "info"),
                    message=getattr(w, "message", ""),
                )
            )

        # Persist cross-model verification warnings (if any)
        for w in cross_model_warnings:
            db.add(
                ValidationWarning(
                    processing_run_id=run.id,
                    field_name=getattr(w, "field_name", None),
                    severity=getattr(w, "severity", "info"),
                    message=getattr(w, "message", ""),
                )
            )

        # 5. Summary (non-fatal)
        try:
            summary_result = llm.summarize(merged_fields, full_text)
            db.add(
                Summary(
                    processing_run_id=run.id,
                    summary_markdown=summary_result.markdown,
                    summary_type="abstractive",
                )
            )
            run.summary_tokens_in = summary_result.tokens_in
            run.summary_tokens_out = summary_result.tokens_out
        except Exception as e:
            db.add(
                ValidationWarning(
                    processing_run_id=run.id,
                    field_name=None,
                    severity="info",
                    message=f"Summary generation failed: {type(e).__name__}: {e}",
                )
            )

        doc.status = "completed"
        db.commit()
        db.refresh(doc)
        return doc

    except HTTPException:
        doc.status = "failed"
        db.commit()
        raise
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)[:500]
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Pipeline failed: {type(e).__name__}: {e}",
        )


class _SimpleWarning:
    """Lightweight stand-in for ValidationWarningInfo when validation itself
    throws — keeps the duck-typed warning loop simple."""

    __slots__ = ("field_name", "severity", "message")

    def __init__(self, field_name: str | None, severity: str, message: str):
        self.field_name = field_name
        self.severity = severity
        self.message = message


def _build_extracted_fields_row(
    run_id: int,
    fields: dict,
    source_page_hints: dict | None = None,
    extraction_notes: str = "",
    confidence: dict[str, float] | None = None,
    origin_per_field: dict[str, str] | None = None,
) -> ExtractedFields:
    """Build the ExtractedFields ORM row from the merged extraction output.

    Stores typed scalar fields on dedicated columns and a JSON blob of the
    full raw extraction (plus page hints / notes) on raw_extraction_json.
    Confidence and per-field origin are stored as JSON blobs.
    """
    f = fields

    def to_decimal(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def to_date(v):
        if v is None:
            return None
        try:
            return datetime.strptime(str(v), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    return ExtractedFields(
        processing_run_id=run_id,
        policy_number=f.get("policy_number"),
        insured_name=f.get("insured_name"),
        insurer_name=f.get("insurer_name"),
        policy_type=f.get("policy_type"),
        effective_date=to_date(f.get("effective_date")),
        expiration_date=to_date(f.get("expiration_date")),
        premium_amount=to_decimal(f.get("premium_amount")),
        premium_frequency=f.get("premium_frequency"),
        face_amount=to_decimal(f.get("face_amount")),
        coverage_limits_json=json.dumps(
            f.get("coverage_limits") or [], default=str
        ),
        exclusions_json=json.dumps(f.get("exclusions") or []),
        raw_extraction_json=json.dumps(
            {
                **f,
                "_source_page_hints": source_page_hints or {},
                "_notes": extraction_notes,
            },
            default=str,
        ),
        confidence_json=json.dumps(confidence or {}, default=str),
        extraction_method_per_field_json=json.dumps(
            origin_per_field or {}, default=str
        ),
    )
