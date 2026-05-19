# Insurance Document Summarizer

A FastAPI service that ingests insurance policy PDFs, extracts structured
fields via a hybrid regex + LLM pipeline (with Tesseract OCR fallback for
scanned PDFs), runs source-grounded validation with per-field confidence
scoring, and produces a human-readable Markdown summary. The LLM layer is
vendor-neutral — pipeline code never imports the Anthropic SDK directly, so
swapping providers or models is a config change rather than a refactor.

> **Build context:** ~1.5 hours wall-clock as a take-home submission, built
> with AI-assisted parallel agent execution (Claude Code orchestrating
> sub-agents per phase). The brief's "Recommended Scope" suggested 45-60
> minutes for implementation; the extra time was spent on the upgrades
> beyond the brief minimum — hybrid regex + LLM extraction, OCR fallback,
> source-grounded validation, 28 tests, mini eval harness, vendor-neutral
> LLM abstraction, and the design rationale doc. Every design decision in
> the rationale is a defensible technical choice, not a tool output.

## Quick Start

### Prerequisites

- Python 3.11+
- `tesseract` (`brew install tesseract` on Mac, `apt install tesseract-ocr` on Linux)
- `poppler` (`brew install poppler` on Mac, `apt install poppler-utils` on Linux)
- Anthropic API key (or skip if you just want the eval harness to run with `--mock`)

### Install

```bash
git clone https://github.com/jerry861200/insurance-summarizer.git
cd insurance-summarizer
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit to set ANTHROPIC_API_KEY, or set LLM_PROVIDER=openai + OPENAI_API_KEY to use OpenAI instead
```

### Run

```bash
uvicorn app.main:app --port 8000 --reload
```

In another terminal:

```bash
curl http://localhost:8000/healthz
open http://localhost:8000/docs  # Swagger UI

# Demo against the sample PDF
curl -X POST http://localhost:8000/documents \
  -F "file=@sample/leland_stanford_policy.pdf" | jq .

# Run the eval harness
python -m eval.run_eval --live --output docs/eval_results.md
```

Expected on the sample: `policy_number = "VF99999990"`,
`insured_name = "LELAND STANFORD"`, `effective_date = "2008-05-01"`,
`face_amount = 500000`, `premium_amount = 36`, `premium_frequency = "monthly"`,
with confidence >= 0.9 on the regex-extracted fields.

## Architecture

```mermaid
flowchart LR
    Client[Client<br/>curl / Swagger] -->|POST PDF| API[FastAPI app]
    API --> PDF[pdf.py<br/>pdfplumber]
    PDF -->|scanned?| OCR[ocr.py<br/>Tesseract fallback]
    PDF & OCR --> Regex[regex.py<br/>cheap deterministic]
    Regex --> Merge{merge<br/>regex wins<br/>on overlap}
    LLM[LLM Provider<br/>Claude Opus 4.7] --> Merge
    Merge --> Validate[validate.py<br/>source-grounding<br/>+ confidence]
    Validate --> Persist[(SQLite<br/>processing_runs<br/>extracted_fields<br/>summaries<br/>validation_warnings)]
    LLM --> Summary[summarize call<br/>templated abstractive]
    Summary --> Persist
    Persist -->|JSON| Client
```

One FastAPI process; one synchronous pipeline per upload. PDF -> optional OCR
-> regex pre-pass -> LLM extraction -> merge (regex wins overlap) ->
source-grounded validation -> persist to SQLite -> LLM summary -> JSON
response. See [docs/architecture.md](docs/architecture.md) for the per-module
breakdown, database schema, and request lifecycle.

## Interview Presentation (60-min flow)

The verbal frame I'd use to walk an interviewer through this project.

**Reading lens — this `main` README is v1 (1.5h take-home).** Each subsection
names the target answer I'd build with more time, then what v1 actually shipped
under the time budget. **Most of the gap was closed in v2 + v3 iterations**
(currently in PR #2 against this branch). Where v3 caught up, I note it inline
as **"→ Caught up in v3 (PR #2): …"**. Where v1's choice was deliberate (not
time-pressed) and still holds, I note **"→ Trigger not fired"** with the
milestone condition.

### 1. Questions I'd ask before designing

Don't draw the architecture cold. Pin down 4 things that materially change
the shape:

1. **Volume:** 10/day or 100k/day? → sync vs async; SQLite vs Postgres.
2. **Latency tolerance:** result inline, or "we'll email you in 5 min"? → 201 immediate vs 202 + poll.
3. **Document mix:** native PDFs or scan-heavy? → OCR is fallback vs primary path.
4. **Downstream consumer:** human broker UI, or feeding a CRM / underwriting engine? → schema rigor + retry semantics.

If the interviewer waves them off: "I'll assume <100/day, sub-minute is fine,
native-mostly, human-facing UI" — and design from there. Every decision below
is **conditional** on those assumptions; flip an assumption, multiple
decisions flip with it.

### 2. The problem isn't "PDF → JSON" — it's three things

1. **Hallucination is a legal risk, not a UX bug.** A wrong `premium_amount` becomes a wrong number on a broker's screen, which becomes the wrong figure in a quote. The whole system is shaped around "always verify, always trace back to source."
2. **Exclusions are nested legal text, not a scalar.** Format varies by insurer; exact-match scoring is brittle. v1 stores them as `list[str]`; semantic eval is on the roadmap. → Caught up in v3 (PR #2): LLM-as-judge in `eval/judge.py` scores them semantically.
3. **Documents are heterogeneous.** Different insurers, different layouts, different field labels. Pure regex doesn't scale; pure LLM hallucinates on novel formats. → Hybrid (D3).

### 3. LLM call strategy — 3 calls is the textbook answer; v1 ships 2

**Target:** 3 targeted calls (fields / exclusions / summary). Each call gets
its own focused schema; one failure doesn't take down the others; summary
prompt can't contaminate extraction.

**v1 shipped (2 calls):** one combined extraction (fields + exclusions +
coverage_limits via Anthropic `tool_use`) plus one summary. Reasoning at MVP scope:

| | 1 mega-call | **v1: 2 (bundled)** | Target: 3 |
|---|---|---|---|
| API cost / latency | 1× | 2× | 3× |
| Reliability — one call fails, others survive | ❌ | ✅ | ✅ |
| Schema enforcement | one giant schema | one focused `tool_use` schema | three small schemas |
| Summary contamination from extraction prompt | yes | no | no |
| Debug "which step broke?" | hard | easy | easiest |

Why we bundled exclusions WITH fields at v1 — and why it might be permanent:

- `tool_use` enforces schema at the API layer. The reliability argument for splitting (one call fails, others survive) is mostly absorbed by API-level enforcement.
- A third call re-feeds the same several-thousand-token `full_text` for marginal quality gain on current eval cases.
- **→ Caught up in v3 (PR #2):** LLM-as-judge (`eval/judge.py`) scores exclusions **semantically at eval time**, so they're evaluated as a separate field even though they're extracted in the same runtime call. This gets us the isolation benefit without paying the third API call at runtime.

### 4. Hallucination defense — target is "Pydantic MVP + prod adds verifier"; v1 shipped past MVP; v3 caught up to the prod target

**Target:** Pydantic schema validation at MVP; production adds a second-pass
validator (regex confirm patterns, dictionary lookup of insurer names,
cross-model verify).

**v1 already past the MVP target — 2 layers shipped:**

1. **Anthropic `tool_use`** = API-level schema enforcement (better than Pydantic + retry — schema is enforced inside the API call, not wrapped after).
2. **Source-grounding validation** (`app/validate.py`): every non-null scalar re-checked against `full_text` with normalized money/date candidates (`$500,000` ≡ `500000` ≡ `500,000.00`). Fields that don't ground → severity-warning + confidence drops to 0.55.

**→ Caught up in v3 (PR #2) — the third layer:**

3. **Cross-model verifier** (`app/extractors/llm/verifier.py`), opt-in via `CROSS_MODEL_VERIFY=true`. Runs both providers, flags scalar disagreements as warnings. Cost is 2×; off by default until "real error rate > 1%" trigger fires.

The system's stance on the LLM: **it's allowed to be wrong, just not silently.**

### 5. API design — target wins on shape; v1 holds on sync

Two API decisions worth calling out explicitly. They cut different ways.

**5a. Per-field response shape — target is right; v1 hasn't migrated yet.**

- **Target:** per-field envelope `{value, source, confidence}` so the client gets all provenance in one nested object per field.
- **v1 shipped:** parallel dicts — `extracted_fields.X`, `confidence.X`, `extraction_method_per_field.X` — keyed by field name. Client cross-references 3-4 maps. This shape came from path-of-least-resistance (each is its own JSON column in SQLAlchemy).
- **→ Partial catch-up in v3 (PR #2):** v3 fixed a silent v1/v2 bug where `confidence` and `extraction_method_per_field` were declared in `DocumentResponse` but never populated by `_document_to_response`. So v3 made the parallel dicts **actually carry data** — but didn't migrate the shape to a nested envelope. The migration is ~30min if a downstream consumer asks for it; nobody has.

**5b. Sync vs async — v1 chose deliberately and still holds.**

- **Target:** POST returns 202 + poll_url, "to leave room for async even if MVP runs sync."
- **v1 shipped:** POST returns 201 + complete result, fully sync (D8).
- **→ Trigger not fired (M5):** 1 broker, 1 PDF, 10-30s wall-clock is well below polling friction. A poll endpoint that always returns immediately = wasted complexity. Async/Celery requires Redis. Trigger condition: >10 docs/min sustained, or 30+ page documents become common. Until then, sync is right even with infinite time.

### 6. Trade-offs at a glance (with v3 status)

| Axis | v1 choice | v3 status (PR #2) |
|---|---|---|
| Extraction | Hybrid regex + LLM (D3) | Unchanged — works |
| LLM call shape | `tool_use` w/ schema | Unchanged — works |
| Vendor | Anthropic-only, abstracted via `LLMProvider` Protocol (D11) | **OpenAI swap** (v1.1 in PR #1) + **cross-model verifier** (v3) |
| API model | Sync 201 (D8) | Unchanged — M5 trigger not fired |
| API per-field shape | Parallel dicts | **Silent bug fixed** (fields now actually populated); envelope migration still open |
| Storage | SQLite + local FS (D9) | Unchanged — M6 trigger not fired; **+ Dockerfile** for any-env deploy |
| Schema shape | Typed columns + JSON blob (D4) | Unchanged |
| Versioning | Append-only `processing_runs` (D7) | Unchanged |
| Summary | Templated abstractive (D5) | Unchanged |
| Source attribution in UI | None (Swagger only) | **"📄 p.N" badges + PDF Preview tab** |
| Eval | 1 hand-labeled case (D10) | **6 cases (1 real + 5 synthetic) + LLM-as-judge + CI regression gate** |
| Frontend | Swagger + curl only | **Streamlit UI** (v2); + batch upload (v3) |
| Tests | 28, mocked LLM | **52** (+verifier, +judge, +frontend smoke) |

(D-numbers reference the "Design Decisions" section below.)

### 7. What v1 deliberately did NOT do (and what v3 did about it)

| Item | v1 reason for deferring | v3 status |
|---|---|---|
| Cross-model verifier (M12) | Writeup right; deferred — engineering ready via D11, no real error signal | ✅ Shipped opt-in via `CROSS_MODEL_VERIFY=true` |
| CI eval gate (M13) | Writeup right; needs eval corpus expansion first | ✅ Shipped (`.github/workflows/ci.yml`, 30% regex baseline floor) |
| Source attribution UI (M14) | Writeup right; full PDF.js bounding-box ~3h — bigger than v1 budget | ⚠️ Partial — page-level badges shipped; span-level still deferred |
| Streamlit / web UI | Writeup right; Swagger + curl is enough for demo, UI is a separate iteration | ✅ Shipped (v2) |
| Real ACORD/COI eval corpus (M4) | Writeup right; couldn't fetch external sources | ⚠️ Partial — 5 diverse synthetic cases; real corpus deferred |
| Multi-vendor LLM (D11 promise) | Writeup right; abstraction was scaffolded but only one provider shipped | ✅ OpenAI provider shipped (v1.1 in PR #1) |
| Auth + RBAC (M1) | Production-only; no real users yet | ❌ Still deferred — no real users |
| Postgres + S3 (M6) | Production-only; 1-line URL swap | ❌ Still deferred — trigger not fired |
| Async / Celery (M5) | Sync is right at MVP scale | ❌ Still deferred — trigger not fired |
| PII redaction (M19) | Production-only; no real PII in demo | ❌ Still deferred — no real PII |

**The principle:** every NOT is a documented decision with a trigger condition,
not a silent omission. v3 closed the gap on most "deferred" items; the rest
are genuinely production-only or trigger-conditional.

### 8. Topics I'd raise before the interviewer asks

The questions a reviewer always lands on:

1. **Cost at scale.** 2 LLM calls × $0.01-0.10/doc. At 100k/mo = mid-4-figures. → Cache by `pdf_sha256` (already indexed, dedupes re-uploads); demote summary to a cheaper model; turn on prompt caching for the system prompt.
2. **Hallucination — three layers, not one.** Schema (`tool_use`) catches format; source-grounding (`validate.py`) catches value errors against the source text. **→ Caught up in v3 (PR #2):** cross-model verifier for the long tail. Production also wants: regex confirm of policy-number patterns, dictionary check of insurer name against NAIC registry.
3. **Exclusions are deceptively hard.** Conditional ("except if…"), time-bound ("during the first 2 years…"), nested ("the exclusion in 5(a) does not apply when…"). v1 stores raw strings — fine for human review. **→ Caught up in v3 (PR #2):** LLM-as-judge scores semantic similarity at eval time. Production still wants: domain-expert taxonomy + structured `Exclusion` row per clause.
4. **PII + compliance.** Policies contain SSN, address, health information. Production: column-level encryption, access log, retention policy, geo-restricted storage (M2, M19). Not in any iteration yet — production-only with clear trigger (first real customer).
5. **How do you know it's correct?** v1: 1 hand-labeled case + per-field scoring with money/date tolerances — the framework. **→ Caught up in v3 (PR #2):** 6 cases, LLM-as-judge for free-text, CI regression gate at 30% baseline. Production target: 100+ hand-labeled ACORD/COI/life/auto/home; re-run on every prompt/model swap.

### 9. One-line thesis

**Treat the LLM as an unreliable contractor: every output needs evidence
(source page), monitoring (confidence + warnings), and the ability to retry
with a different model.** The architecture isn't about clever prompting — it's
about systematically constraining where the LLM is allowed to be wrong
silently. v1 ships the constraint layer at MVP scope; v3 (PR #2) extends the
same constraint thinking into UX, ops, and eval — same thesis, deeper coverage.

## Design Decisions

Eleven decisions, each in "Chose X over Y because Z. With more time: W (M#)."
format. Numbers in parentheses cite milestones in the post-MVP roadmap.

1. **PDF extraction (native path):** Chose **`pdfplumber` with `layout=True`**
   over `PyPDF2` or going straight to AWS Textract because the sample PDF is
   native-text and `pdfplumber` preserves multi-column reading order and
   table structure that the LLM uses to disambiguate fields. `PyPDF2` returns
   text with broken reading order on multi-column documents; Textract is
   accurate but adds cloud dependency and per-page cost for documents that
   don't need it. With more time: AWS Textract or Azure Document Intelligence
   (the latter has pre-built insurance models) for higher accuracy on
   complex layouts (M3).

2. **Scanned PDF handling:** Chose **Tesseract via `pdf2image` + `pytesseract`
   as a runtime fallback** over making OCR a required step or upgrading
   straight to a cloud OCR. The pipeline detects scanned PDFs by character
   density (`chars_per_page < 100`) and only invokes OCR when needed —
   native-text PDFs never pay the OCR latency. Tesseract auto-locates its
   binary across PATH, Homebrew, and Linux paths so it works in sandboxed
   environments. With more time: AWS Textract or Azure Document Intelligence
   for accuracy on real-world scans (M3).

3. **Information extraction:** Chose **hybrid regex + LLM** over pure LLM or
   pure regex because regex catches `policy_number`, `effective_date`,
   `expiration_date`, `premium_amount`, and `face_amount` at near-zero cost
   when the format matches (the Leland Stanford sample fills 5/5), while the
   LLM handles semantic fields (`insurer_name` variants, `exclusions`,
   `coverage_limits`) and novel insurer formats. Regex wins on overlap because
   it's deterministic and can't hallucinate. With more time: per-insurer
   prompt overrides for the top N carriers (M8) and cross-model verification
   on high-stakes fields (M12).

4. **Field schema:** Chose **typed columns + raw JSON blob** on
   `extracted_fields` over either-or because typed columns enable indexed
   queries (`WHERE policy_number = X`) for broker tools, while
   `raw_extraction_json` preserves the full LLM output so new schema fields
   don't require migrations or re-extraction. `confidence_json` and
   `extraction_method_per_field_json` are also JSON blobs because they're
   per-run, per-field metadata that doesn't justify its own table. With more
   time: Postgres `JSONB` with a GIN index gives both queries and document
   flexibility without giving up the relational model.

5. **Summarization:** Chose **templated abstractive** over extractive because
   policy legalese reads badly verbatim and the broker audience needs the
   same sections in the same place every time. The summary prompt forces a
   fixed Markdown structure (Policy Summary header, Key Terms, Notable
   Exclusions & Conditions, Things to Verify) so the LLM can't omit a
   category like exclusions, and verbatim field substitution for
   IDs/dates/money means the broker always sees consistent numbers. With more
   time: citation-augmented summaries where every fact links back to its
   source page (M14).

6. **Hallucination defense:** Chose **source-grounding validation** over
   trusting the LLM's `tool_use` output. `validate.py` re-checks every
   non-null field against the source text — dates and money are normalized
   to candidate forms (`$500,000`, `500000`, `500,000.00`, etc.) — and any
   field that doesn't ground gets a warning and confidence drops to 0.55.
   This makes silent hallucination essentially impossible on the fields where
   it matters (IDs, dates, money, names). With more time: cross-model
   verification with a cheap second model (M12) for the long tail.

7. **Versioning:** Chose **append-only `processing_runs`** with auto-incremented
   `run_number` over updating `extracted_fields` in place because losing
   history makes prompt iteration unsafe — I can't tell whether a new prompt
   is better without an old extraction to compare. Append-only also gives me
   a free audit trail (which model/prompt produced which fields when), and
   the `pdf_sha256` index sets up duplicate-upload short-circuiting. With
   more time: a cold-storage policy that archives runs older than the last
   3 per document once volume justifies it (M15).

8. **API model:** Chose **synchronous request handling** over an async
   queue with Celery + Redis because the demo target is one broker, one PDF
   at a time, and pipeline wall-clock is 10-30 seconds — well under the
   threshold where polling becomes friction. Async adds Redis as a hard
   dependency and a polling protocol for clients, both of which would have
   surface-failed during the interview demo. With more time: Celery workers
   with `POST` returning 202 + job ID once volume crosses 10 docs/min or
   30-page documents become common (M5).

9. **Storage:** Chose **SQLite + local filesystem** over Postgres + S3
   because the entire stack starts with `git clone && pip install` — no
   infrastructure to provision, no credentials to configure for the demo,
   and the schema is a one-line URL swap to Postgres when needed
   (`DATABASE_URL=postgresql://...`). PDF storage is similarly one
   `storage.save_pdf_bytes()` implementation change to use boto3 + presigned
   URLs. With more time: Postgres + S3 with the same SQLAlchemy ORM (M6).

10. **LLM model choice:** Chose **Claude Opus 4.7 for development and
    Sonnet 4.6 for production**, single model for both extraction and
    summary calls, switchable via `ANTHROPIC_MODEL` env var. Opus 4.7 in
    dev because cost is $0.5-2 total across all MVP and eval runs — trivial
    versus the interview signal of "I picked max quality and have a clear
    cost-optimization path." Two-models-for-two-calls would have doubled
    the debugging surface for marginal token savings at MVP scale. With
    more time: benchmark Haiku 4.5 for extraction-only against Sonnet using
    the eval harness (M10) once monthly LLM bill > $1k.

11. **LLM provider abstraction:** Chose **`LLMProvider` Protocol with a
    factory** over importing the Anthropic SDK directly in `pipeline.py`
    because vendor lock-in is a real procurement risk in regulated industries
    (insurance, healthcare). All LLM calls go through
    `app.extractors.llm.base.LLMProvider`; the concrete `AnthropicProvider`
    lives in its own file and `get_llm_provider(settings)` selects by config.
    Swapping to OpenAI or a local Llama is a new file in
    `app/extractors/llm/` with zero pipeline changes; mocking in tests is
    replacing one Protocol implementation with a stub. Cost is ~50 lines;
    value is every vendor/model decision becomes a config change. No
    obvious upgrade — this is one of the few decisions where the MVP
    version IS the production version.

## What's Out of Scope

- **Web UI** — Swagger UI + curl are the demo surface
- **Auth / multi-tenancy / per-org isolation** — no real users yet (M1)
- **Async processing** — sync is fine for 1 broker, 1 PDF (M5)
- **Postgres + S3** — 1-line URL swap when needed (M6)
- **AWS Textract / Azure Document Intelligence** — Tesseract handles MVP scanning (M3)
- **PII redaction, i18n, prompt caching, multi-pass verification, per-insurer prompts** — all tracked in the milestone roadmap

## What I'd Build Next

Top 5 from the post-MVP roadmap:

1. **M1 — Auth + RBAC.** OAuth2 / API keys with per-org row-level isolation. Blocker for any real user.
2. **M2 — Encryption at rest + audit log.** DB column encryption for PII, file encryption at rest, who-accessed-what log. Compliance pre-launch.
3. **M3 — OCR upgrade.** Swap Tesseract for AWS Textract or Azure Document Intelligence (Azure has pre-built insurance models for ACORD forms). Drop-in replacement for `app/ocr.py`.
4. **M4 — Eval harness expansion (5 -> 50+ PDFs).** ACORD forms + COI + life + auto + home. Gate CI on accuracy regression vs baseline.
5. **M5 — Async processing.** Celery + Redis; `POST` returns 202 + job ID; client polls status. Trigger: > 10 docs/min sustained or 30-page documents become common.

The full roadmap (M1-M22) with tiers, effort, value type, and trigger
conditions is in the plan document.

## Project Structure

```
insurance-summarizer/
|-- README.md                              This file
|-- requirements.txt
|-- pyproject.toml                         Ruff + pytest config
|-- .env.example
|-- app/
|   |-- main.py                            FastAPI factory + /healthz
|   |-- config.py                          pydantic-settings
|   |-- routes.py                          HTTP endpoints
|   |-- pipeline.py                        Orchestrator
|   |-- pdf.py                             pdfplumber + scanned detection
|   |-- ocr.py                             Tesseract fallback
|   |-- validate.py                        Source-grounding + confidence
|   |-- schemas.py                         Pydantic API contracts
|   |-- models.py                          SQLAlchemy ORM (5 tables)
|   |-- storage.py                         DB engine + file helpers
|   `-- extractors/
|       |-- regex.py                       Deterministic pre-pass
|       `-- llm/
|           |-- base.py                    LLMProvider Protocol
|           |-- prompts.py                 Vendor-neutral prompts + tool schema
|           `-- anthropic_provider.py      Anthropic SDK impl
|-- tests/                                 28 tests across pdf/regex/validate/routes
|-- eval/                                  Eval harness + golden PDFs
|-- sample/leland_stanford_policy.pdf      Sample for demo
`-- docs/
    |-- architecture.md
    |-- design_rationale.md
    `-- sample_summary.md
```

## Tests

The test suite covers `app/pdf.py` (6 tests), `app/extractors/regex.py` (7),
`app/validate.py` (6), and the FastAPI routes (9) for 28 tests total. The
route tests use a mock `LLMProvider` so CI doesn't burn API calls, and the
integration test confirms end-to-end that `POST /documents` against the
Leland Stanford PDF returns `policy_number = "VF99999990"`. Run with:

```bash
pytest tests/ -v
```

## Deeper Reading

- [docs/architecture.md](docs/architecture.md) — mermaid diagram, per-module breakdown, database schema, request lifecycle, error handling
- [docs/design_rationale.md](docs/design_rationale.md) — ~3000 words organized by the brief's "Technical Considerations" headings; each subsection has a decision, trade-off table, production trade-off, and an interview Q&A
- [docs/sample_summary.md](docs/sample_summary.md) — example Markdown summary output on the Leland Stanford sample

## License

Built as a take-home case study for interview evaluation.
