from __future__ import annotations
import json
import httpx
import pytest
import respx

from app.extractors.llm.openai_provider import OpenAIProvider, _to_openai_function
from app.extractors.llm.prompts import EXTRACTION_TOOL_SCHEMA


# Canned OpenAI chat.completions response with a tool_call
def _make_tool_call_response(arguments: dict) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_test",
                    "type": "function",
                    "function": {
                        "name": "save_policy_fields",
                        "arguments": json.dumps(arguments),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _make_text_response(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
    }


@respx.mock
def test_extract_fields_parses_tool_call_response():
    fixture = {
        "policy_number": "TEST-12345",
        "insured_name": "Test User",
        "insurer_name": "Test Insurance Co",
        "policy_type": "Term Life",
        "effective_date": "2024-01-01",
        "expiration_date": "2034-01-01",
        "premium_amount": 100.0,
        "premium_frequency": "monthly",
        "premium_currency": "USD",
        "face_amount": 250000.0,
        "coverage_limits": [],
        "exclusions": ["Suicide within 2 years"],
        "source_page_hints": {"policy_number": [1]},
        "extraction_notes": "",
    }
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_make_tool_call_response(fixture))
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    result = provider.extract_fields("dummy text")

    assert result.fields["policy_number"] == "TEST-12345"
    assert result.fields["face_amount"] == 250000.0
    assert result.source_page_hints == {"policy_number": [1]}
    assert result.extraction_notes == ""
    assert result.tokens_in == 100
    assert result.tokens_out == 50
    assert result.model_id == "gpt-4o"
    # Confirm source_page_hints and extraction_notes were popped out of fields
    assert "source_page_hints" not in result.fields
    assert "extraction_notes" not in result.fields


@respx.mock
def test_summarize_returns_markdown():
    markdown = "## Policy Summary\n\n**Policy** TEST-12345\n"
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_make_text_response(markdown))
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    result = provider.summarize({"policy_number": "TEST-12345"}, "source text")

    assert result.markdown == markdown.strip()
    assert result.tokens_in == 200
    assert result.tokens_out == 80
    assert result.model_id == "gpt-4o"


def test_factory_returns_openai_provider_when_configured(monkeypatch):
    # Force a fresh Settings read
    from app.config import get_settings
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    get_settings.cache_clear()
    settings = get_settings()

    from app.extractors.llm import get_llm_provider
    provider = get_llm_provider(settings)

    assert type(provider).__name__ == "OpenAIProvider"
    assert provider.model == "gpt-4o"

    get_settings.cache_clear()  # cleanup for other tests


def test_to_openai_function_adapts_anthropic_schema():
    """Verify the schema adapter preserves all required fields and just rewraps."""
    openai_fn = _to_openai_function(EXTRACTION_TOOL_SCHEMA)

    assert openai_fn["type"] == "function"
    assert openai_fn["function"]["name"] == "save_policy_fields"
    assert openai_fn["function"]["description"] == EXTRACTION_TOOL_SCHEMA["description"]
    # Parameters should be the original input_schema
    assert openai_fn["function"]["parameters"] == EXTRACTION_TOOL_SCHEMA["input_schema"]
    # Required fields preserved
    assert "policy_number" in openai_fn["function"]["parameters"]["required"]
    assert len(openai_fn["function"]["parameters"]["required"]) == len(EXTRACTION_TOOL_SCHEMA["input_schema"]["required"])
