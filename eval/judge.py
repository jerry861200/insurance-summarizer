"""LLM-as-judge for free-text eval fields.

Some fields (summary_markdown, exclusion clauses, etc.) cannot reasonably be
scored by exact match or substring overlap - the same meaning can be expressed
many ways. This module provides a lightweight "judge" that asks the active
LLMProvider to score semantic similarity between two strings on a 0.0-1.0
scale and returns a structured result.

Design notes:
- The judge intentionally reuses the project's existing `LLMProvider`
  abstraction so we don't introduce a second vendor surface inside the eval
  harness. We piggy-back on `LLMProvider.summarize()` (a free-text in,
  free-text out path) by feeding it the judge prompt as the "source document"
  and a tiny instruction fields dict. The model returns a JSON object that we
  parse.
- This is an EVAL TOOL, never called from the production pipeline. Real LLM
  calls cost money - tests MUST mock the judge function (see
  `tests/test_judge.py` for the pattern).
- If the model returns malformed JSON, we degrade to score=0.0 with the raw
  response in the `reason` field so a human can debug.

Usage:
    from eval.judge import judge_semantic_similarity
    from app.extractors.llm import get_llm_provider
    from app.config import get_settings

    llm = get_llm_provider(get_settings())
    result = judge_semantic_similarity(
        field="exclusions",
        expected="suicide within 2 years; war",
        actual="Suicide during the first 24 months; military conflict",
        llm=llm,
    )
    print(result.score, result.reason)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.extractors.llm import LLMProvider


@dataclass
class JudgeResult:
    """Outcome of a single judge call.

    Attributes:
        score: 0.0 (unrelated) to 1.0 (semantically equivalent).
        reason: one-sentence rationale from the judge model.
    """

    score: float
    reason: str


JUDGE_PROMPT = """You are scoring whether two text fields from an insurance document are SEMANTICALLY equivalent.

Field name: {field}

EXPECTED:
{expected}

ACTUAL:
{actual}

Return a JSON object with this shape:
{{"score": <float 0.0-1.0>, "reason": "<one sentence>"}}

Scoring guide:
- 1.0: same meaning, possibly different wording/order
- 0.75: mostly same; minor missing detail
- 0.5: partial overlap; significant gaps
- 0.25: tangentially related; mostly wrong
- 0.0: unrelated or contradictory

Output ONLY the JSON object, no commentary."""


# Tolerant JSON extractor: the model may wrap the object in a code fence,
# add a leading explanation, etc. We just want the first valid {...} we can
# parse. Greedy match against the first '{' and the last '}' is good enough
# for a one-line response.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_judge_response(raw: str) -> JudgeResult:
    """Best-effort parse of a judge response into a JudgeResult.

    Returns a JudgeResult with score=0.0 and the raw response as reason when
    parsing fails - never raises, so a single bad response never sinks an
    eval run.
    """
    if not raw:
        return JudgeResult(score=0.0, reason="empty judge response")
    match = _JSON_BLOCK_RE.search(raw)
    if match is None:
        return JudgeResult(score=0.0, reason=f"no JSON in response: {raw[:120]}")
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return JudgeResult(score=0.0, reason=f"invalid JSON ({e.msg}): {raw[:120]}")

    try:
        score = float(obj.get("score", 0.0))
    except (TypeError, ValueError):
        return JudgeResult(score=0.0, reason=f"score not numeric: {obj.get('score')!r}")
    # Clamp to [0, 1] - judges occasionally return e.g. 1.1 or -0.1.
    score = max(0.0, min(1.0, score))
    reason = str(obj.get("reason", "")).strip() or "(no reason given)"
    return JudgeResult(score=score, reason=reason)


def judge_semantic_similarity(
    field: str, expected: str, actual: str, llm: LLMProvider
) -> JudgeResult:
    """Ask the active LLMProvider to score expected vs actual on [0, 1].

    Args:
        field: name of the field being judged (for prompt context).
        expected: the gold value (string-cast).
        actual: the extracted value (string-cast).
        llm: any LLMProvider implementation (Anthropic, OpenAI, or a stub).

    Returns:
        JudgeResult. Never raises - parse failures degrade to score=0.0.

    NOTE: this hits the configured LLM provider. Tests must inject a stub
    (see eval.metrics.compare_semantic which takes a judge_fn callable).
    """
    prompt = JUDGE_PROMPT.format(field=field, expected=expected, actual=actual)
    # Reuse the existing summarize path - it takes free text in, returns
    # free text out, which is exactly what a judge needs. The fields dict
    # carries a brief instruction the provider's system prompt will see.
    summary = llm.summarize(
        fields={"judge_instruction": "Return ONLY the JSON object scoring semantic similarity."},
        full_text=prompt,
    )
    return _parse_judge_response(summary.markdown)
