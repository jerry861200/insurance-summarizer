from __future__ import annotations
import json
from anthropic import Anthropic
from app.extractors.llm.base import LLMProvider, ExtractionResult, SummaryResult, EXTRACTION_PROMPT_VERSION, SUMMARY_PROMPT_VERSION
from app.extractors.llm.prompts import EXTRACTION_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT, EXTRACTION_TOOL_SCHEMA


class AnthropicProvider:
    """LLMProvider impl using the Anthropic Python SDK."""

    def __init__(self, model: str, api_key: str):
        self.model = model
        self._client = Anthropic(api_key=api_key)

    def extract_fields(self, full_text: str) -> ExtractionResult:
        """Call Claude with tool_use forcing save_policy_fields. Returns structured fields."""
        user_message = f"Extract the structured fields from the following insurance policy document.\n\n<document>\n{full_text}\n</document>"

        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=EXTRACTION_SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "save_policy_fields"},
            messages=[{"role": "user", "content": user_message}],
        )

        # Find the tool_use content block
        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise RuntimeError("Anthropic did not return a tool_use block")

        raw = tool_use_block.input  # dict
        source_page_hints = raw.pop("source_page_hints", {}) or {}
        extraction_notes = raw.pop("extraction_notes", "") or ""

        return ExtractionResult(
            fields=raw,
            source_page_hints=source_page_hints,
            extraction_notes=extraction_notes,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model_id=self.model,
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )

    def summarize(self, fields: dict, full_text: str) -> SummaryResult:
        user_message = (
            f"Produce the summary for this policy.\n\n"
            f"Extracted fields:\n{json.dumps(fields, indent=2, default=str)}\n\n"
            f"Source document (for reference; do not introduce facts not in the extracted fields):\n{full_text}"
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        # Get the text content
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        markdown = "\n".join(text_blocks).strip()

        return SummaryResult(
            markdown=markdown,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model_id=self.model,
            prompt_version=SUMMARY_PROMPT_VERSION,
        )
