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
from app.models import Document, ExtractedFields, ProcessingRun
from app.pdf import extract_full_text, extract_pages, is_likely_scanned
from app.storage import save_pdf_bytes


def process(
    file_bytes: bytes,
    filename: str,
    db: Session,
    llm: LLMProvider,
) -> Document:
    """Run the core pipeline on uploaded PDF bytes.

    Phase B scope: save → extract text → LLM extract → persist.
    OCR fallback, regex pre-pass, validation, and summary land in Phase C.
    """
    settings = get_settings()

    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Not a valid PDF (missing %PDF header)")
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
            raise HTTPException(status_code=422, detail="PDF has no extractable pages")

        doc.page_count = len(pages)
        doc.extraction_method = "scanned-unhandled" if is_likely_scanned(pages) else "native"

        full_text = extract_full_text(pages)

        if doc.extraction_method == "scanned-unhandled":
            # Phase C2 wires real OCR. For now, still try the LLM with whatever text we have.
            pass

        started = datetime.now(timezone.utc)
        result: ExtractionResult = llm.extract_fields(full_text)
        completed = datetime.now(timezone.utc)

        llm_fields_filled = sum(
            1
            for k, v in result.fields.items()
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
            regex_fields_filled=0,
            llm_fields_filled=llm_fields_filled,
            started_at=started,
            completed_at=completed,
            status="success",
        )
        db.add(run)
        db.flush()

        ef = _build_extracted_fields_row(run.id, result)
        db.add(ef)

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
        raise HTTPException(status_code=502, detail=f"Pipeline failed: {type(e).__name__}: {e}")


def _build_extracted_fields_row(run_id: int, result: ExtractionResult) -> ExtractedFields:
    f = result.fields

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
        coverage_limits_json=json.dumps(f.get("coverage_limits") or [], default=str),
        exclusions_json=json.dumps(f.get("exclusions") or []),
        raw_extraction_json=json.dumps(
            {**f, "_source_page_hints": result.source_page_hints, "_notes": result.extraction_notes},
            default=str,
        ),
    )
