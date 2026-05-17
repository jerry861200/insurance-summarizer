from __future__ import annotations
from app.config import Settings
from app.extractors.llm.base import LLMProvider, ExtractionResult, SummaryResult
from app.extractors.llm.anthropic_provider import AnthropicProvider


def get_llm_provider(settings: Settings) -> LLMProvider:
    """Factory: select provider based on settings.llm_provider."""
    match settings.llm_provider:
        case "anthropic":
            return AnthropicProvider(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
        case "openai":
            from app.extractors.llm.openai_provider import OpenAIProvider
            return OpenAIProvider(model=settings.openai_model, api_key=settings.openai_api_key)
        case _:
            raise ValueError(f"Unknown llm_provider: {settings.llm_provider}")


__all__ = ["LLMProvider", "ExtractionResult", "SummaryResult", "AnthropicProvider", "OpenAIProvider", "get_llm_provider"]
