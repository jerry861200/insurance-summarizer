from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.extractors.llm import ExtractionResult, LLMProvider, get_llm_provider
from app.extractors.llm.base import SUMMARY_PROMPT_VERSION
from app.models import Document, ExtractedFields, ProcessingRun
from app.pdf import extract_full_text, extract_pages, is_likely_scanned
from app.pipeline import _build_extracted_fields_row, process as run_pipeline
from app.schemas import (
    CoverageLimit,
    DocumentListItem,
    DocumentResponse,
    ExtractedFieldsSchema,
)
from app.storage import get_db

router = APIRouter()


def _get_llm(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return get_llm_provider(settings)


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int


class ReprocessRequest(BaseModel):
    reason: str | None = Field(default=None)


@router.post("/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(_get_llm),
) -> DocumentResponse:
    filename = file.filename or "uploaded.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Expected a .pdf file")

    file_bytes = await file.read()
    doc = run_pipeline(file_bytes, filename, db, llm)
    return _document_to_response(doc)


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    base_q = select(Document).where(Document.deleted_at.is_(None))
    if status is not None:
        base_q = base_q.where(Document.status == status)

    # Count total matching rows (before pagination)
    count_q = select(Document.id).where(Document.deleted_at.is_(None))
    if status is not None:
        count_q = count_q.where(Document.status == status)
    total = len(db.execute(count_q).all())

    ordered_q = (
        base_q.order_by(Document.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
    )
    docs = db.execute(ordered_q).scalars().all()

    items = [
        DocumentListItem(
            id=d.id,
            filename=d.filename,
            status=d.status,
            uploaded_at=d.uploaded_at,
            page_count=d.page_count,
        )
        for d in docs
    ]
    return DocumentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    db: Session = Depends(get_db),
) -> DocumentResponse:
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_to_response(doc)


@router.get("/documents/{document_id}/pdf")
def get_document_pdf(
    document_id: str,
    db: Session = Depends(get_db),
):
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not Path(doc.pdf_path).exists():
        raise HTTPException(status_code=404, detail="Source PDF missing from storage")
    return FileResponse(
        doc.pdf_path,
        media_type="application/pdf",
        filename=doc.filename,
    )


@router.post("/documents/{document_id}/reprocess", response_model=DocumentResponse)
def reprocess_document(
    document_id: str,
    body: ReprocessRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(_get_llm),
) -> DocumentResponse:
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not Path(doc.pdf_path).exists():
        raise HTTPException(status_code=404, detail="Source PDF missing from storage")

    _reprocess_existing(doc, db, llm)
    db.refresh(doc)
    return _document_to_response(doc)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
):
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return None


def _reprocess_existing(doc: Document, db: Session, llm: LLMProvider) -> Document:
    """Re-run extraction on an existing document. Inserts a new ProcessingRun
    targeting the same Document.id (does NOT create a new document)."""
    # Required Phase C modules (always available now)
    from app.extractors.regex import merge_with_llm, regex_extract
    from app.models import Summary, ValidationWarning

    # Optional modules — defensive imports for partial state
    try:
        from app.ocr import ocr_pdf  # type: ignore
    except ImportError:  # pragma: no cover
        ocr_pdf = None  # type: ignore

    try:
        from app.validate import validate as run_validation  # type: ignore
        _VALIDATE_AVAILABLE = True
    except ImportError:  # pragma: no cover
        _VALIDATE_AVAILABLE = False

    pdf_path = doc.pdf_path

    try:
        pages = extract_pages(pdf_path)
        if not pages:
            raise HTTPException(status_code=422, detail="PDF has no extractable pages")

        # OCR fallback for scanned documents, if module is available.
        if is_likely_scanned(pages) and ocr_pdf is not None:
            try:
                pages = ocr_pdf(pdf_path)
                doc.extraction_method = "ocr"
            except Exception:
                doc.extraction_method = "scanned-unhandled"
        elif is_likely_scanned(pages):
            doc.extraction_method = "scanned-unhandled"
        else:
            doc.extraction_method = "native"

        doc.page_count = len(pages)

        full_text = extract_full_text(pages)

        # Re-hash bytes to keep checksum in sync (file may have been re-uploaded).
        try:
            doc.pdf_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
        except OSError:
            pass

        # Regex pre-pass (deterministic, cheap)
        regex_fields = regex_extract(full_text)

        # LLM extraction
        started = datetime.now(timezone.utc)
        result: ExtractionResult = llm.extract_fields(full_text)
        completed = datetime.now(timezone.utc)

        # Merge: regex wins on overlap, with origin tracking
        merged_fields, origin_per_field = merge_with_llm(regex_fields, result.fields)

        # Validate (if available)
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
            except Exception:
                pass

        regex_fields_filled = sum(
            1 for v in regex_fields.values() if v not in (None, [], {}, "")
        )
        llm_fields_filled = sum(
            1 for v in result.fields.values() if v not in (None, [], {}, "")
        )

        next_run_number = (
            max((r.run_number for r in doc.processing_runs), default=0) + 1
        )

        run = ProcessingRun(
            document_id=doc.id,
            run_number=next_run_number,
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

        # Summary (non-fatal)
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
        doc.error_message = None
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
            detail=f"Reprocess failed: {type(e).__name__}: {e}",
        )


def _document_to_response(doc: Document) -> DocumentResponse:
    latest_run = (
        max(doc.processing_runs, key=lambda r: r.run_number)
        if doc.processing_runs
        else None
    )

    extracted = None
    summary_md = None
    run_id = None
    prompt_versions: dict[str, str] = {}
    model_id = None

    if latest_run is not None:
        run_id = latest_run.id
        prompt_versions = {
            "extraction": latest_run.extraction_prompt_version,
            "summary": latest_run.summary_prompt_version,
        }
        model_id = latest_run.model_id

        if latest_run.extracted_fields is not None:
            ef = latest_run.extracted_fields
            coverage = json.loads(ef.coverage_limits_json) if ef.coverage_limits_json else []
            exclusions = json.loads(ef.exclusions_json) if ef.exclusions_json else []
            extracted = ExtractedFieldsSchema(
                policy_number=ef.policy_number,
                insured_name=ef.insured_name,
                insurer_name=ef.insurer_name,
                policy_type=ef.policy_type,
                effective_date=ef.effective_date,
                expiration_date=ef.expiration_date,
                premium_amount=ef.premium_amount,
                premium_frequency=ef.premium_frequency,
                face_amount=ef.face_amount,
                coverage_limits=[
                    CoverageLimit(
                        name=c.get("name", ""),
                        limit_amount=c.get("limit_amount"),
                        currency=c.get("currency", "USD"),
                        notes=c.get("notes"),
                    )
                    for c in coverage
                ],
                exclusions=exclusions,
            )

        if latest_run.summary is not None:
            summary_md = latest_run.summary.summary_markdown

    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        status=doc.status,
        uploaded_at=doc.uploaded_at,
        page_count=doc.page_count,
        extraction_method=doc.extraction_method,
        extracted_fields=extracted,
        summary_markdown=summary_md,
        warnings=[],
        processing_run_id=run_id,
        prompt_versions=prompt_versions,
        model_id=model_id,
    )
