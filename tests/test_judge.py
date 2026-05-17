"""Tests for the LLM-as-judge utility and its `compare_semantic` consumer.

These tests NEVER hit a real LLM. They either pass a hand-built JudgeResult
through a fake judge_fn into `compare_semantic`, or they construct a stub
LLMProvider that returns a canned `SummaryResult` (so the JSON-parsing path
in `judge_semantic_similarity` is exercised without API calls).
"""
from __future__ import annotations

import pytest

from app.extractors.llm import SummaryResult
from eval.judge import (
    JUDGE_PROMPT,
    JudgeResult,
    _parse_judge_response,
    judge_semantic_similarity,
)
from eval.metrics import compare_semantic, evaluate_case


# -------------------- compare_semantic (the framework integration) --------------------


def test_compare_semantic_high_score_passes():
    def fake_judge(field, exp, act):
        return JudgeResult(score=0.9, reason="essentially same")
    r = compare_semantic(
        "summary",
        "Policy covers life insurance for John.",
        "John's policy provides life coverage.",
        fake_judge,
    )
    assert r.passed is True
    assert r.score == 0.9
    assert "essentially same" in r.note


def test_compare_semantic_low_score_fails():
    def fake_judge(field, exp, act):
        return JudgeResult(score=0.3, reason="different meaning")
    r = compare_semantic(
        "summary",
        "Policy covers life insurance.",
        "Policy excludes life coverage.",
        fake_judge,
    )
    assert r.passed is False
    assert r.score == 0.3


def test_compare_semantic_handles_none():
    # judge should never be called when one side is None - we use a
    # lambda that would raise to prove it.
    def boom(*_):
        raise AssertionError("judge should not be called when both are None")
    r = compare_semantic("summary", None, None, boom)
    assert r.passed is True
    assert r.score == 1.0


def test_compare_semantic_one_sided_none_fails():
    def fake_judge(field, exp, act):
        return JudgeResult(score=1.0, reason="x")  # would pass if called
    r = compare_semantic("summary", "expected text", None, fake_judge)
    assert r.passed is False
    assert r.score == 0.0
    assert "missing" in r.note


# -------------------- _parse_judge_response (parsing robustness) --------------------


def test_parse_judge_response_well_formed():
    raw = '{"score": 0.82, "reason": "covers same exclusions in different wording"}'
    result = _parse_judge_response(raw)
    assert result.score == 0.82
    assert "covers same exclusions" in result.reason


def test_parse_judge_response_in_code_fence():
    raw = 'Here is my judgment:\n```json\n{"score": 0.6, "reason": "partial"}\n```\n'
    result = _parse_judge_response(raw)
    assert result.score == 0.6


def test_parse_judge_response_score_clamped():
    # Model occasionally returns out-of-range. Verify we clamp not crash.
    high = _parse_judge_response('{"score": 1.4, "reason": "too high"}')
    low = _parse_judge_response('{"score": -0.2, "reason": "too low"}')
    assert high.score == 1.0
    assert low.score == 0.0


def test_parse_judge_response_invalid_json_degrades_gracefully():
    result = _parse_judge_response("not JSON at all")
    assert result.score == 0.0
    assert "no JSON" in result.reason or "invalid JSON" in result.reason


def test_parse_judge_response_empty_string():
    result = _parse_judge_response("")
    assert result.score == 0.0


# -------------------- judge_semantic_similarity end-to-end (with stub LLM) --------------------


class _StubJudgeLLM:
    """Stub LLMProvider whose summarize() returns a canned JSON judge response.
    Tracks calls so we can assert the prompt was constructed correctly.
    """

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[dict, str]] = []

    def extract_fields(self, full_text):  # pragma: no cover - not exercised here
        raise NotImplementedError

    def summarize(self, fields, full_text):
        self.calls.append((fields, full_text))
        return SummaryResult(
            markdown=self.response,
            tokens_in=10, tokens_out=20,
            model_id="stub", prompt_version="1.0",
        )


def test_judge_semantic_similarity_uses_llm_and_parses():
    llm = _StubJudgeLLM('{"score": 0.95, "reason": "near-identical meaning"}')
    result = judge_semantic_similarity(
        field="exclusions",
        expected="suicide; war",
        actual="Suicide; armed conflict",
        llm=llm,
    )
    assert result.score == 0.95
    assert "near-identical" in result.reason
    # Sanity-check the prompt was actually sent through summarize
    assert len(llm.calls) == 1
    _fields, prompt = llm.calls[0]
    assert "exclusions" in prompt
    assert "suicide; war" in prompt
    assert "Suicide; armed conflict" in prompt


def test_judge_semantic_similarity_malformed_response_returns_zero():
    llm = _StubJudgeLLM("the model decided to ignore instructions")
    result = judge_semantic_similarity(
        field="summary", expected="A", actual="B", llm=llm,
    )
    assert result.score == 0.0


# -------------------- evaluate_case integration with judge_fn --------------------


def test_evaluate_case_without_judge_fn_skips_semantic_fields():
    expected = {
        "policy_number": "X-1",
        "exclusions_must_include_substrings": ["war"],
    }
    actual = {"policy_number": "X-1", "exclusions": ["death from war"]}
    cr = evaluate_case(expected, actual, "no-judge")
    # No 'exclusions_semantic' result should be present.
    assert all(fr.field_name != "exclusions_semantic" for fr in cr.field_results)


def test_evaluate_case_with_judge_fn_adds_semantic_result():
    expected = {
        "policy_number": "X-1",
        "exclusions_must_include_substrings": ["war"],
    }
    actual = {"policy_number": "X-1", "exclusions": ["death from war"]}

    def fake_judge(field, exp, act):
        # Pretend the judge thinks "war" -> "death from war" is a strong match
        assert field == "exclusions_semantic"
        assert "war" in exp and "war" in act
        return JudgeResult(score=0.85, reason="captures the war exclusion")

    cr = evaluate_case(expected, actual, "with-judge", judge_fn=fake_judge)
    semantic = [fr for fr in cr.field_results if fr.field_name == "exclusions_semantic"]
    assert len(semantic) == 1
    assert semantic[0].passed is True
    assert semantic[0].score == 0.85


def test_evaluate_case_judge_skipped_when_no_exclusions():
    # Empty expected exclusions list -> no judging (avoid scoring noise)
    expected = {
        "policy_number": "X-1",
        "exclusions_must_include_substrings": [],
    }
    actual = {"policy_number": "X-1", "exclusions": ["death from war"]}

    def boom(*_):
        raise AssertionError("judge should not be called when expected has no substrings")

    cr = evaluate_case(expected, actual, "empty-judge", judge_fn=boom)
    # Substring check still runs and passes (0 required = trivially satisfied)
    assert any(fr.field_name == "exclusions" for fr in cr.field_results)
    # No semantic FieldResult appended
    assert all(fr.field_name != "exclusions_semantic" for fr in cr.field_results)


# -------------------- JUDGE_PROMPT structure sanity --------------------


def test_judge_prompt_has_required_placeholders():
    # Smoke test: format() works with field/expected/actual keys.
    out = JUDGE_PROMPT.format(field="x", expected="y", actual="z")
    assert "x" in out and "y" in out and "z" in out
    assert "0.0" in out and "1.0" in out
