from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.extractors.llm import LLMProvider, get_llm_provider
from app.models import Document
from app.pipeline import process as run_pipeline
from app.schemas import CoverageLimit, DocumentResponse, ExtractedFieldsSchema
from app.storage import get_db

router = APIRouter()


def _get_llm(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return get_llm_provider(settings)


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


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    db: Session = Depends(get_db),
) -> DocumentResponse:
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_to_response(doc)


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
