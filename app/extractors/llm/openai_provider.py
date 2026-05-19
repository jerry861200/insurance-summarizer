from __future__ import annotations
import json
from openai import OpenAI
from app.extractors.llm.base import (
    LLMProvider, ExtractionResult, SummaryResult,
    EXTRACTION_PROMPT_VERSION, SUMMARY_PROMPT_VERSION,
)
from app.extractors.llm.prompts import (
    EXTRACTION_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT, EXTRACTION_TOOL_SCHEMA,
)


def _to_openai_function(anthropic_tool_schema: dict) -> dict:
    """Adapt Anthropic tool_use schema -> OpenAI function-call schema.

    Anthropic format: {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI format:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    return {
        "type": "function",
        "function": {
            "name": anthropic_tool_schema["name"],
            "description": anthropic_tool_schema["description"],
            "parameters": anthropic_tool_schema["input_schema"],
        },
    }


class OpenAIProvider:
    """LLMProvider impl using OpenAI Python SDK (function calling).

    Reuses the SAME prompts and tool schema as AnthropicProvider — the schema
    is just wrapped differently per vendor. This is the D12 vendor-abstraction
    paying off: nothing about extraction logic differs.
    """

    def __init__(self, model: str, api_key: str):
        self.model = model
        self._client = OpenAI(api_key=api_key)
        self._tool = _to_openai_function(EXTRACTION_TOOL_SCHEMA)

    def extract_fields(self, full_text: str) -> ExtractionResult:
        user_message = (
            f"Extract the structured fields from the following insurance policy document.\n\n"
            f"<document>\n{full_text}\n</document>"
        )
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            tools=[self._tool],
            tool_choice={"type": "function", "function": {"name": "save_policy_fields"}},
        )
        choice = response.choices[0]
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            raise RuntimeError("OpenAI did not return a tool_call (model may have ignored tool_choice)")
        raw = json.loads(tool_calls[0].function.arguments)
        source_page_hints = raw.pop("source_page_hints", {}) or {}
        extraction_notes = raw.pop("extraction_notes", "") or ""
        return ExtractionResult(
            fields=raw,
            source_page_hints=source_page_hints,
            extraction_notes=extraction_notes,
            tokens_in=response.usage.prompt_tokens,
            tokens_out=response.usage.completion_tokens,
            model_id=self.model,
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )

    def summarize(self, fields: dict, full_text: str) -> SummaryResult:
        user_message = (
            f"Produce the summary for this policy.\n\n"
            f"Extracted fields:\n{json.dumps(fields, indent=2, default=str)}\n\n"
            f"Source document (for reference; do not introduce facts not in the extracted fields):\n{full_text}"
        )
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        return SummaryResult(
            markdown=(response.choices[0].message.content or "").strip(),
            tokens_in=response.usage.prompt_tokens,
            tokens_out=response.usage.completion_tokens,
            model_id=self.model,
            prompt_version=SUMMARY_PROMPT_VERSION,
        )
