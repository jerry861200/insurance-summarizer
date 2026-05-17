"""Eval harness CLI. Usage:

    python -m eval.run_eval                  # regex-only (no LLM, no API key)
    python -m eval.run_eval --mock           # mock LLM (deterministic, no API key)
    python -m eval.run_eval --live           # real Claude API (requires ANTHROPIC_API_KEY)
    python -m eval.run_eval --live --judge   # also use LLM-as-judge for free-text fields
    python -m eval.run_eval --output docs/eval_results.md  # write Markdown report

The --judge flag is only meaningful with --live (it needs a real provider
to call). It is silently disabled when paired with --mock or no provider flag.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.extractors.llm import ExtractionResult, LLMProvider, SummaryResult
from app.extractors.regex import merge_with_llm, regex_extract
from app.pdf import extract_full_text, extract_pages

from eval.metrics import CaseResult, JudgeFn, evaluate_case, render_report

GOLDEN_DIR = Path(__file__).parent / "golden"


class _RegexOnlyProvider:
    """No-LLM fallback: returns empty fields. The pipeline still gets
    regex-extracted values via merge_with_llm."""
    def extract_fields(self, full_text: str) -> ExtractionResult:
        return ExtractionResult(
            fields={},
            source_page_hints={},
            extraction_notes="(regex-only - no LLM)",
            tokens_in=0, tokens_out=0,
            model_id="regex-only", prompt_version="1.0",
        )
    def summarize(self, fields: dict, full_text: str) -> SummaryResult:
        return SummaryResult(markdown="(no summary in regex-only mode)",
                             tokens_in=0, tokens_out=0,
                             model_id="regex-only", prompt_version="1.0")


class _MockProvider:
    """Deterministic mock that returns hardcoded Leland Stanford values.
    Useful for verifying the eval harness itself works without API calls."""
    def extract_fields(self, full_text: str) -> ExtractionResult:
        return ExtractionResult(
            fields={
                "policy_number": "VF99999990",
                "insured_name": "LELAND STANFORD",
                "insurer_name": "Pacific Life Insurance Company",
                "policy_type": "Term Life",
                "effective_date": "2008-05-01",
                "expiration_date": "2068-05-01",
                "premium_amount": 36.0,
                "premium_frequency": "monthly",
                "premium_currency": "USD",
                "face_amount": 500000.0,
                "coverage_limits": [{"name": "Face Amount", "limit_amount": 500000.0, "currency": "USD"}],
                "exclusions": ["Suicide within 2 years of policy date"],
            },
            source_page_hints={"policy_number": [1], "effective_date": [1]},
            extraction_notes="",
            tokens_in=0, tokens_out=0,
            model_id="mock", prompt_version="1.0",
        )
    def summarize(self, fields: dict, full_text: str) -> SummaryResult:
        return SummaryResult(markdown="(mock summary)",
                             tokens_in=0, tokens_out=0,
                             model_id="mock", prompt_version="1.0")


def _load_cases() -> list[dict]:
    """Load every {name}.json in eval/golden/ that has a matching {name}.pdf."""
    cases = []
    for json_file in sorted(GOLDEN_DIR.glob("*.json")):
        with json_file.open() as f:
            spec = json.load(f)
        pdf_path = GOLDEN_DIR / spec["pdf_filename"]
        if not pdf_path.exists():
            print(f"WARN: skipping {json_file.name} - PDF {spec['pdf_filename']} not found", file=sys.stderr)
            continue
        spec["_pdf_path"] = str(pdf_path)
        cases.append(spec)
    return cases


def _run_extraction(pdf_path: str, llm: LLMProvider) -> dict:
    """Run the extraction pipeline (regex + LLM merge) on a PDF.
    Returns the merged fields dict - same shape as expected schema."""
    pages = extract_pages(pdf_path)
    full_text = extract_full_text(pages)
    regex_fields = regex_extract(full_text)
    llm_result = llm.extract_fields(full_text)
    merged, _origin = merge_with_llm(regex_fields, llm_result.fields)
    return merged


def _build_judge_fn(llm: LLMProvider) -> JudgeFn:
    """Build a JudgeFn bound to the active LLMProvider.

    Imported lazily so eval.judge's imports don't cost anything when --judge
    is off.
    """
    from eval.judge import judge_semantic_similarity

    def _judge(field: str, expected: str, actual: str):
        return judge_semantic_similarity(field, expected, actual, llm)

    return _judge


def main() -> int:
    parser = argparse.ArgumentParser(description="Run extraction eval harness")
    parser.add_argument("--mock", action="store_true", help="Use hardcoded mock LLM (deterministic)")
    parser.add_argument("--live", action="store_true", help="Use real Anthropic API (requires ANTHROPIC_API_KEY)")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Enable LLM-as-judge for free-text fields (exclusions, summaries). "
             "Only meaningful with --live; ignored otherwise.",
    )
    parser.add_argument("--output", type=str, default=None, help="Write Markdown report to this path (also prints to stdout)")
    args = parser.parse_args()

    if args.mock and args.live:
        print("ERROR: --mock and --live are mutually exclusive", file=sys.stderr)
        return 2

    if args.live:
        from app.config import get_settings
        from app.extractors.llm import get_llm_provider
        settings = get_settings()
        if not settings.anthropic_api_key and settings.llm_provider == "anthropic":
            print("ERROR: --live requires ANTHROPIC_API_KEY in .env", file=sys.stderr)
            return 2
        if not settings.openai_api_key and settings.llm_provider == "openai":
            print("ERROR: --live with LLM_PROVIDER=openai requires OPENAI_API_KEY in .env", file=sys.stderr)
            return 2
        llm = get_llm_provider(settings)
        model_id = settings.anthropic_model if settings.llm_provider == "anthropic" else settings.openai_model
        mode = f"live ({settings.llm_provider}/{model_id})"
    elif args.mock:
        llm = _MockProvider()
        mode = "mock (deterministic)"
    else:
        llm = _RegexOnlyProvider()
        mode = "regex-only (no LLM)"

    # --judge only makes sense with a real provider. Warn loudly when it
    # would be silently ignored rather than just dropping it on the floor.
    judge_fn: JudgeFn | None = None
    if args.judge:
        if args.live:
            judge_fn = _build_judge_fn(llm)
            mode += " + judge"
        else:
            print(
                "WARN: --judge requires --live to call a real LLM; ignoring --judge",
                file=sys.stderr,
            )

    print(f"# Eval mode: {mode}\n")

    cases = _load_cases()
    if not cases:
        print("ERROR: no golden cases found", file=sys.stderr)
        return 2

    case_results: list[CaseResult] = []
    for case in cases:
        print(f"Running case: {case['case_name']} ...")
        actual = _run_extraction(case["_pdf_path"], llm)
        cr = evaluate_case(case["expected"], actual, case["case_name"], judge_fn=judge_fn)
        case_results.append(cr)
        print(f"  -> {cr.passed_count}/{cr.total_count} passed, score {cr.overall_score:.2%}")

    report = render_report(case_results)
    print("\n" + report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.live:
            cmd = "python -m eval.run_eval --live" + (" --judge" if args.judge else "")
        elif args.mock:
            cmd = "python -m eval.run_eval --mock"
        else:
            cmd = "python -m eval.run_eval"
        output_path.write_text(f"<!-- Generated by `{cmd}` -->\n\n")
        with output_path.open("a") as f:
            f.write(report)
        print(f"\nReport written to: {output_path}")

    return 0 if all(cr.overall_passed for cr in case_results) else 1


if __name__ == "__main__":
    sys.exit(main())
