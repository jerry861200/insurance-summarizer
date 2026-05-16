from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MoneyAmount(BaseModel):
    value: Decimal | None = None
    currency: str = "USD"
    raw_text: str | None = None


class DateField(BaseModel):
    value: date | None = None
    raw_text: str | None = None


class CoverageLimit(BaseModel):
    name: str
    limit_amount: Decimal | None = None
    currency: str = "USD"
    notes: str | None = None


class ValidationWarningSchema(BaseModel):
    field: str | None = None
    severity: Literal["info", "warning", "error"]
    message: str


class ExtractedFieldsSchema(BaseModel):
    policy_number: str | None = None
    insured_name: str | None = None
    insurer_name: str | None = None
    policy_type: str | None = None
    effective_date: date | None = None
    expiration_date: date | None = None
    premium_amount: Decimal | None = None
    premium_frequency: str | None = None
    face_amount: Decimal | None = None
    coverage_limits: list[CoverageLimit] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class DocumentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: str
    uploaded_at: datetime
    page_count: int | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: str
    uploaded_at: datetime
    page_count: int | None = None
    extraction_method: str | None = None
    extracted_fields: ExtractedFieldsSchema | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    extraction_method_per_field: dict[str, str] = Field(default_factory=dict)
    summary_markdown: str | None = None
    warnings: list[ValidationWarningSchema] = Field(default_factory=list)
    processing_run_id: int | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model_id: str | None = None
