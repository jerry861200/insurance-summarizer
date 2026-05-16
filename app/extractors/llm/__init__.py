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
            # Placeholder for future OpenAIProvider; raise for now
            raise NotImplementedError("OpenAI provider not yet implemented")
        case _:
            raise ValueError(f"Unknown llm_provider: {settings.llm_provider}")


__all__ = ["LLMProvider", "ExtractionResult", "SummaryResult", "AnthropicProvider", "get_llm_provider"]
