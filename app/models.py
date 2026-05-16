from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    pdf_path: Mapped[str] = mapped_column(String(512))
    pdf_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size_bytes: Mapped[int] = mapped_column()
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    processing_runs: Mapped[list[ProcessingRun]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), index=True
    )
    run_number: Mapped[int] = mapped_column()
    extraction_prompt_version: Mapped[str] = mapped_column(String(10))
    summary_prompt_version: Mapped[str] = mapped_column(String(10))
    model_id: Mapped[str] = mapped_column(String(50))
    extraction_tokens_in: Mapped[int] = mapped_column(default=0)
    extraction_tokens_out: Mapped[int] = mapped_column(default=0)
    summary_tokens_in: Mapped[int] = mapped_column(default=0)
    summary_tokens_out: Mapped[int] = mapped_column(default=0)
    regex_fields_filled: Mapped[int] = mapped_column(default=0)
    llm_fields_filled: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="success")

    document: Mapped[Document] = relationship(back_populates="processing_runs")
    extracted_fields: Mapped[ExtractedFields | None] = relationship(
        back_populates="processing_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    summary: Mapped[Summary | None] = relationship(
        back_populates="processing_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    validation_warnings: Mapped[list[ValidationWarning]] = relationship(
        back_populates="processing_run",
        cascade="all, delete-orphan",
    )


class ExtractedFields(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    processing_run_id: Mapped[int] = mapped_column(
        ForeignKey("processing_runs.id"), unique=True, index=True
    )
    policy_number: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    insured_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insurer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    premium_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    premium_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    face_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    coverage_limits_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclusions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_extraction_json: Mapped[str] = mapped_column(Text)
    confidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method_per_field_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    processing_run: Mapped[ProcessingRun] = relationship(
        back_populates="extracted_fields"
    )


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    processing_run_id: Mapped[int] = mapped_column(
        ForeignKey("processing_runs.id"), unique=True, index=True
    )
    summary_markdown: Mapped[str] = mapped_column(Text)
    summary_type: Mapped[str] = mapped_column(String(20), default="abstractive")

    processing_run: Mapped[ProcessingRun] = relationship(back_populates="summary")


class ValidationWarning(Base):
    __tablename__ = "validation_warnings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    processing_run_id: Mapped[int] = mapped_column(
        ForeignKey("processing_runs.id"), index=True
    )
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)

    processing_run: Mapped[ProcessingRun] = relationship(
        back_populates="validation_warnings"
    )
