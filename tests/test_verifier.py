"""Tests for the cross-model verifier (app/extractors/llm/verifier.py).

The verifier is provider-agnostic: it takes an `LLMProvider`-conforming
object as the secondary, so we mock at the Protocol boundary with simple
stub objects rather than at the HTTP layer. Same isolation as respx, less
ceremony, and it doesn't depend on which providers happen to be installed.
"""
from __future__ import annotations

from app.extractors.llm.base import ExtractionResult
from app.extractors.llm.verifier import Disagreement, verify_extraction


def _make_result(fields: dict, model_id: str = "stub") -> ExtractionResult:
    return ExtractionResult(
        fields=fields,
        source_page_hints={},
        extraction_notes="",
        tokens_in=0,
        tokens_out=0,
        model_id=model_id,
        prompt_version="1.0",
    )


class _StubProvider:
    """Tiny LLMProvider stub. Returns the canned fields whenever
    extract_fields is called. summarize() is not exercised by the verifier."""

    def __init__(self, fields: dict, model_id: str = "stub-secondary"):
        self._fields = fields
        self._model_id = model_id
        self.calls = 0

    def extract_fields(self, full_text: str) -> ExtractionResult:
        self.calls += 1
        return _make_result(dict(self._fields), self._model_id)

    def summarize(self, fields: dict, full_text: str):  # pragma: no cover
        raise NotImplementedError("Not used by verifier tests")


# Shared "agreement" payload used as a baseline across multiple tests
_AGREE_FIELDS = {
    "policy_number": "ABC-123",
    "insured_name": "Leland Stanford",
    "insurer_name": "Pacific Life Insurance Company",
    "effective_date": "2008-05-01",
    "expiration_date": "2068-05-01",
    "premium_amount": 36.0,
    "face_amount": 500000.0,
}


def test_verify_no_disagreements_when_providers_agree():
    """When both providers return identical scalars, the disagreement list
    should be empty. Confirms the happy path doesn't emit false positives."""
    primary = _make_result(dict(_AGREE_FIELDS), model_id="stub-primary")
    secondary = _StubProvider(dict(_AGREE_FIELDS))

    disagreements = verify_extraction(primary, "any text", secondary)

    assert disagreements == []
    assert secondary.calls == 1


def test_verify_flags_policy_number_disagreement():
    """A 1-char policy_number diff should produce exactly one Disagreement
    with the field_name and both values surfaced."""
    primary_fields = dict(_AGREE_FIELDS, policy_number="ABC-123")
    secondary_fields = dict(_AGREE_FIELDS, policy_number="ABC-124")

    primary = _make_result(primary_fields)
    secondary = _StubProvider(secondary_fields)

    disagreements = verify_extraction(primary, "any text", secondary)

    assert len(disagreements) == 1
    d = disagreements[0]
    assert isinstance(d, Disagreement)
    assert d.field_name == "policy_number"
    assert d.primary_value == "ABC-123"
    assert d.secondary_value == "ABC-124"


def test_verify_skips_when_secondary_field_is_none():
    """If the secondary returns None for a field, that's 'no signal' and must
    NOT be reported as a disagreement (avoids noise from one model missing a
    field the other found)."""
    primary_fields = dict(_AGREE_FIELDS, premium_amount=36.0)
    secondary_fields = dict(_AGREE_FIELDS, premium_amount=None)

    primary = _make_result(primary_fields)
    secondary = _StubProvider(secondary_fields)

    disagreements = verify_extraction(primary, "any text", secondary)

    # premium_amount disagreement must NOT appear
    fields_flagged = {d.field_name for d in disagreements}
    assert "premium_amount" not in fields_flagged
    # And since everything else agrees, the list is fully empty
    assert disagreements == []


def test_verify_money_within_tolerance():
    """Money fields use a 0.01 tolerance — 100.00 vs 100.005 must NOT trigger
    a disagreement (sub-penny rounding noise)."""
    primary_fields = dict(_AGREE_FIELDS, premium_amount=100.00)
    secondary_fields = dict(_AGREE_FIELDS, premium_amount=100.005)

    primary = _make_result(primary_fields)
    secondary = _StubProvider(secondary_fields)

    disagreements = verify_extraction(primary, "any text", secondary)

    fields_flagged = {d.field_name for d in disagreements}
    assert "premium_amount" not in fields_flagged
    assert disagreements == []
