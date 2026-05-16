# Evaluation Harness

Offline evaluation against known-good PDFs. Tracks per-field extraction accuracy
across prompt and model changes. This is the "how do I know my extraction is
correct?" answer.

## Quick start

```bash
# Mock LLM (deterministic - verifies the harness pipeline itself works)
python -m eval.run_eval --mock

# Regex-only (no LLM, no API key - measures what the cheap path catches)
python -m eval.run_eval

# Real Claude (requires ANTHROPIC_API_KEY in .env)
python -m eval.run_eval --live --output docs/eval_results.md
```

## Adding a new golden case

1. Drop a PDF into `eval/golden/<name>.pdf`
2. Create `eval/golden/<name>.json` with:

```json
{
  "case_name": "<name>",
  "pdf_filename": "<name>.pdf",
  "description": "Human-readable note about the document",
  "expected": {
    "policy_number": "...",
    "insured_name": "...",
    "effective_date": "YYYY-MM-DD",
    "expiration_date": "YYYY-MM-DD",
    "premium_amount": 0.0,
    "premium_frequency": "monthly|annual|...",
    "face_amount": 0.0,
    "exclusions_must_include_substrings": ["suicide", "fraud"]
  }
}
```

3. Run the harness - your case is picked up automatically.

## Comparison logic

Per `eval/metrics.py`:

- **Strings** (policy_number, names): exact match (case-insensitive), partial
  credit for substring match.
- **Dates**: tolerance +/-1 day.
- **Money**: tolerance +/-$0.01 exact, partial credit at +/-5%.
- **Exclusion lists**: all expected substrings must appear in some entry of
  the extracted exclusions list.

## What's NOT in MVP (see roadmap milestones)

- Only 1 golden case (Leland Stanford). Production needs 50+ across ACORD forms,
  life/auto/home variety - see milestone M4.
- No CI integration - see M13.
- No log-prob-based confidence - heuristic only.
- No labeled review queue for low-confidence extractions.
