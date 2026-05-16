from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

EXTRACTION_PROMPT_VERSION = "1.0"
SUMMARY_PROMPT_VERSION = "1.0"


@dataclass
class ExtractionResult:
    """What an LLMProvider.extract_fields() returns. Vendor-neutral."""
    fields: dict                            # raw structured extraction (matches the tool schema)
    source_page_hints: dict[str, list[int]] = field(default_factory=dict)
    extraction_notes: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    model_id: str = ""
    prompt_version: str = EXTRACTION_PROMPT_VERSION


@dataclass
class SummaryResult:
    """What an LLMProvider.summarize() returns."""
    markdown: str
    tokens_in: int = 0
    tokens_out: int = 0
    model_id: str = ""
    prompt_version: str = SUMMARY_PROMPT_VERSION


class LLMProvider(Protocol):
    """Vendor-neutral interface. Implementations live in <vendor>_provider.py."""

    def extract_fields(self, full_text: str) -> ExtractionResult: ...

    def summarize(self, fields: dict, full_text: str) -> SummaryResult: ...
