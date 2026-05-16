# Design Rationale

Comprehensive design discussion for the Insurance Document Summarizer. Organized
by the brief's "Technical Considerations" headings so each question maps to a
section. For implementation details and the architecture diagram, see
[architecture.md](architecture.md).

---

## 1. Document Processing

### 1.1 Native vs scanned PDFs

**Decision:** I detect scanned PDFs at runtime using a character-density
heuristic (`pdf.is_likely_scanned()`: average chars/page < 100) and fall back to
Tesseract OCR via `pytesseract` + `pdf2image`. Native-text PDFs go through
`pdfplumber` directly. The chosen path is recorded in
`documents.extraction_method` (`native` / `ocr` / `scanned-unhandled`) so I
can later report accuracy by source.

**Trade-off table:**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `pdfplumber` only | Zero infra; fast on native PDFs; preserves layout | Returns empty text on scanned PDFs | Use for native path |
| Tesseract via `pdf2image` | Free; runs locally; "good enough" for typed scans | Slow (~10-30s/page at 200 DPI); poor on complex layouts; English-only by default | Use as fallback only |
| AWS Textract / Azure Document Intelligence | High accuracy; table + form models; Azure has pre-built insurance models | Per-page pricing; cloud dependency; setup friction for a take-home | Defer to M3 |
| Vision LLM (Claude Vision) | Single API call; handles complex layouts | $$$ per page; latency; overkill for typed docs | Defer to M17 |

**Why for this exercise:** The sample PDF (Leland Stanford Pacific Life term
life) is a native-text PDF with 14 pages and ~5K tokens — `pdfplumber` extracts
it cleanly. I needed an OCR story to remove the "what about scanned PDFs?"
objection in interview, but I didn't want to make Tesseract a hard dependency
that would block the demo if the reviewer hasn't installed it. The pipeline
auto-locates the binary across PATH, `/opt/homebrew/bin` (Apple Silicon),
`/usr/local/bin`, and `/usr/bin` (Linux), so it works wherever it lands.

**Production trade-off:** Tesseract's accuracy degrades sharply on noisy scans,
handwriting, and dense tables (premium schedules in particular). For production
I'd swap to AWS Textract or Azure Document Intelligence (M3) — Azure has
pre-built insurance models specifically tuned for ACORD forms and certificates
of insurance. The `app/ocr.py` interface returns the same `list[PageText]` shape
as `app/pdf.py`, so the swap is one file.

**Interview defense:**
> *Q: Why a heuristic? Why not check for embedded text directly?*
> *A: I do — `chars_per_page < 100` is exactly that check. PDFs with no extractable text return zero or near-zero chars per page from pdfplumber. The threshold isn't 0 because some "mostly-scanned" PDFs have a sparse cover page of native text but the body is scanned. 100 is conservative: the sample PDF averages ~2,500 chars/page, so there's no false-positive risk on normal documents.*

### 1.2 Preprocessing (OCR, text normalization, layout analysis)

**Decision:** I preprocess minimally — `pdfplumber.extract_text(layout=True)`
preserves multi-column layout and table structure, and I concatenate pages with
`--- PAGE N ---` markers so the LLM can cite source pages. I deliberately do
NOT normalize headers, footers, page numbers, or unicode quirks because the
LLM tolerates these and aggressive normalization risks dropping signal.

**Trade-off table:**

| Preprocessing step | What it costs | What it buys | Decision |
|---|---|---|---|
| `layout=True` | ~2x parse time | Preserves multi-column reading order; tables stay readable | Yes (used) |
| Page markers (`--- PAGE N ---`) | None | Lets LLM populate `source_page_hints` for downstream linkage (M14) | Yes (used) |
| Strip headers/footers | Custom rules per insurer | Cleaner LLM input | No — risks dropping the policy number which often lives in a header |
| Unicode NFKC normalization | Trivial cost | Cleaner regex matching | No — `dateparser` and the LLM handle variants fine |
| Dehyphenation across line breaks | Heuristic | Marginal LLM gain | No — LLM does this for us |
| Table extraction (`extract_tables()`) | Complex; format-specific | Critical for premium schedule data | Defer to M18 (section-aware chunking) |

**Why for this exercise:** I treated preprocessing as a place to apply the
*minimum viable cleaning* — the LLM is more tolerant of messy input than a
brittle regex pipeline, and every transformation I do is a transformation I
might do wrong on insurer #2. The `--- PAGE N ---` markers are the one
non-trivial choice: they cost nothing to add and unlock per-field source-page
linkage (`extracted_fields.raw_extraction_json._source_page_hints`), which sets
up the production source-grounded UI (M14) where a broker clicks a field and
the corresponding page highlights.

**Production trade-off:** Premium schedules on multi-page life policies (the
sample has a 60-year premium table) benefit from `pdfplumber.extract_tables()`
or a dedicated table-extraction model. I'd add per-section preprocessing once I
detect that "premium ratio over time" is a question brokers actually ask. Until
then, the LLM reads the schedule from `layout=True` text well enough to surface
"premium increases steeply" in the summary's "Things to Verify" section.

**Interview defense:**
> *Q: You're sending the LLM raw text with headers and footers — isn't that wasteful?*
> *A: For a 14-page document, headers/footers add maybe 5% to the token count. At Sonnet 4.6 prices (~$3/M input tokens) that's a rounding error. I'd rather pay 5% more tokens than maintain insurer-specific stripping rules that break on new templates. If usage scales to 1M docs/month, M11 (prompt caching) and M18 (section-aware chunking) both attack this — different lever, same problem.*

### 1.3 Multi-page documents with varying structures

**Decision:** Single LLM call per document with all pages concatenated and
`--- PAGE N ---` markers. The sample PDF is 14 pages and fits comfortably in
the Sonnet 4.6 / Opus 4.7 context window (200K tokens) at ~5K tokens of input.
I do not chunk, route by section, or run page-by-page extraction.

**Trade-off table:**

| Approach | Cost | When it wins | When it breaks |
|---|---|---|---|
| Single call, all pages | 1x LLM call | Documents under ~50 pages with whole-document structure | 100+ page docs (cost + latency); narrow context windows |
| Page-by-page extraction | N× LLM calls | When pages are independent (e.g., separate riders) | Wastes tokens on cross-page fields (face_amount referenced once on page 1, conditions on page 8) |
| Map-reduce (extract per page → reconcile) | N + 1 calls | Very long docs; parallelizable | Cross-page conflicts hard to reconcile; ~2x cost |
| RAG (vector retrieval over chunks) | Embed + retrieve + extract | Docs > 100 pages where most pages are boilerplate | Setup complexity; risks missing fields that don't match query |

**Why for this exercise:** A single call works because the sample is 14 pages.
Per-page extraction would double the cost (one call per page) and force the
model to resolve cross-page references in app code instead of in its head. The
`source_page_hints` field in the extraction tool schema captures the
page-attribution metadata I'd otherwise get from per-page calls.

**Production trade-off:** Once documents exceed ~50 pages or the 200K context
window, I'd build M16 (RAG) — embed each page, retrieve the top-K relevant
chunks for each target field, then run a focused extraction call. M18
(section-aware chunking) is the lighter-weight precursor: detect section
headers ("Definitions", "Exclusions", "Premium Schedule") and route each
section to a section-specialized prompt.

**Interview defense:**
> *Q: What's your breaking point on document size?*
> *A: At ~50 pages I'd worry about per-call latency more than context window — Sonnet handles 200K tokens, but the wall-clock for a single extraction creeps toward 30s. At 100+ pages, M16 (RAG) becomes the architecture. The current pipeline assumes the entire doc fits and stays under ~10K input tokens; everything else is a milestone with a clear trigger.*

---

## 2. Information Extraction

### 2.1 Rule-based vs LLM-based extraction

**Decision:** Hybrid. I run a deterministic regex pre-pass for fields with
strong patterns (`policy_number`, `effective_date`, `expiration_date`,
`premium_amount`, `face_amount`), then call the LLM to fill everything else
(`insurer_name`, `policy_type`, `coverage_limits`, `exclusions`, etc.). On
overlap, regex wins because it's deterministic and can't hallucinate.

**Trade-off table:**

| Approach | Cost / call | Accuracy on patterned fields | Accuracy on semantic fields | Verdict |
|---|---|---|---|---|
| Pure regex | $0 | High when format matches; brittle across insurers | Near zero (can't summarize exclusions) | Insufficient alone |
| Pure LLM | ~$0.02/doc on Sonnet, ~$0.10 on Opus | High but with hallucination risk | High | Wasteful on patterned fields |
| Hybrid (regex first, LLM fills) | LLM call + microseconds for regex | Near 100% (regex catches the common case) | High (LLM handles edge fields) | Chosen |
| Cross-model verification (M12) | 2x LLM cost | Highest | Highest | Defer until field error > 1% |

**Why for this exercise:** The brief explicitly calls out "inconsistent formats
across insurers" as the challenge. Pure regex can't solve that — the LLM does.
But once I'm calling an LLM, paying for it to extract `policy_number = "VF99999990"`
from `Policy Number: VF99999990` is wasteful when a one-line regex does the
same job for zero dollars and zero hallucination risk. The hybrid pattern is
the production answer for document AI: deterministic extraction wherever the
format allows, LLM wherever it doesn't.

In my regex coverage:
- `policy_number`: 3 patterns (`Policy Number`, `Policy ID`, `Contract Number`), filtered to require at least one digit and length >= 4 to reject noise like `Policy Number: ABCDEFG`.
- Dates: 2 patterns (`Policy/Effective/Inception Date`, `Expiration/Expiry/Maturity/Coverage End Date`) routed through `dateparser` to handle "May 1, 2008", "05/01/2008", "1st May 2008", etc.
- Money: `Face Amount`, `Death Benefit`, `Sum Insured` for `face_amount`; `Premium` (with optional `Initial`/`Monthly`/`Annual` qualifier) for `premium_amount`. Range-checked `0 < val < 1e9`.

**Production trade-off:** Per-insurer prompt overrides (M8) for the top N
carriers we see in production traffic — once an insurer accounts for >5% of
our volume, we can hand-tune both the regex patterns and the LLM prompt for
their template. Eval-gated rollout via `eval/run_eval.py` ensures these
overrides don't regress accuracy on other insurers.

**Interview defense:**
> *Q: How do you handle regex false positives?*
> *A: Two ways. Each regex has post-filters (policy_number requires a digit and length >= 4; money is range-checked 0 to $1B). And source-grounding validation re-checks every extracted value against the source text — so even if regex pulls the wrong policy number from a header, if it can't be re-located in the document body the confidence drops to 0.55 and we flag it.*

### 2.2 Cross-insurer format inconsistency

**Decision:** Layered defense — I rely on multiple mechanisms instead of one
clever trick, because no single layer handles every insurer template:

1. **LLM tool schema** (`EXTRACTION_TOOL_SCHEMA`) defines canonical field names; the LLM maps insurer-specific labels to them.
2. **Prompt aliases** — the system prompt enumerates known aliases ("Face Amount", "Sum Insured", "Coverage Amount", "Death Benefit" all map to `face_amount`).
3. **Normalization in the prompt** — dates to ISO 8601, money to plain decimals, frequency to enum.
4. **`dateparser`** in regex handles dozens of natural-language date formats.
5. **Document type implicit in `policy_type`** — the LLM tells us what kind of policy it is, so downstream logic can branch.
6. **Per-insurer overrides (M8)** — future hook for templates that need special handling.
7. **Eval harness coverage** (`eval/`) — 5 PDFs in the MVP, scaling to 50+ in M4. This is the only mechanism that proves the others work.

**Why for this exercise:** Cross-insurer variance is the most-asked
interview question about document AI, and there's no silver bullet. The
honest answer is "layered defenses + an eval harness to measure what works."
I chose to ship all 7 layers in some form rather than over-investing in one —
the eval harness (D10 in the plan, M4 for expansion) is the lever that lets me
add layer 6 (per-insurer overrides) safely later.

**Production trade-off:** The biggest gap today is layer 6 (per-insurer
overrides) and the eval harness only covers 5 PDFs. M4 expands to 50+ PDFs
spanning ACORD forms, life, auto, and home — that's a 2-week investment that
unlocks safe iteration on layers 2 and 6.

**Interview defense:**
> *Q: A new insurer uses a completely novel layout — what happens?*
> *A: The LLM handles novel layouts well — that's its job. The regex pre-pass might miss its targets and the LLM picks them up. Source-grounding catches hallucinations. The remaining risk is "the LLM extracts the wrong thing confidently and source-grounding doesn't catch it because the wrong value also appears in the text." That's where the eval harness comes in: once we have signal, we expand the golden set to include this insurer's template, measure baseline accuracy, then iterate on prompt aliases or per-insurer overrides until we hit the threshold.*

### 2.3 Validation mechanisms

**Decision:** Four layers in MVP, three more deferred:

| Layer | Implementation | Status |
|---|---|---|
| 1. Format checks | `_format_check()` in `validate.py` — ISO dates, money range, policy_number non-empty | MVP |
| 2. Cross-field checks | `_cross_field_checks()` — `effective_date < expiration_date`, suspicious premium/face ratio | MVP |
| 3. Source-grounding | `_is_grounded_in_source()` — every non-null value must appear in source text (normalized for money/dates) | MVP |
| 4. Confidence scoring | 5-tier scale (1.0/0.95/0.85/0.55/0.0) based on grounding + page hint + origin | MVP |
| 5. Business rules | "Term life premiums shouldn't decrease over the schedule"; per-LOB rules | Deferred — broker-tuned |
| 6. Cross-model verification | Second LLM call cross-checks the extraction | Deferred to M12 |
| 7. Human review queue | UI surfaces low-confidence rows | Deferred to M7 (Web UI) |

Source-grounding is the strongest layer and the one I'm proudest of. Concrete
example: for `face_amount = 500000`, the validator builds candidate strings
(`500,000`, `500000`, `500,000.00`, `500000.00`) and checks if any appears in
the source. Same for dates: ISO + slash + month-name in multiple casings. If
nothing matches, the field is flagged with `"possible LLM hallucination"` and
confidence drops to 0.55. This makes "the LLM made up a value" essentially
impossible to do silently.

**Trade-off table:**

| Layer | Effort | Risk if absent |
|---|---|---|
| Format check | Low | Garbage propagates downstream |
| Cross-field | Low | Logic errors (expiration before effective) survive |
| Source-grounding | Medium | LLM hallucination is undetected |
| Confidence | Low | Users can't tell which fields to trust |
| Business rules | High (LOB-specific) | Domain-specific errors slip through |
| Cross-model verification | High ($$) | High-stakes field errors |
| Human review | UI + workflow | Edge cases never improve |

**Why for this exercise:** The first 4 layers are the right investment for an
MVP. Layers 5-7 require broker input, infrastructure, and traffic to be worth
building. The 5-tier confidence scale is intentionally coarse — finer
granularity (e.g., 100 levels) signals false precision when the underlying
signal is binary (grounded or not, format-valid or not).

**Production trade-off:** M12 (cross-model verification) is where I'd go first
once we have signal that the top extraction is wrong > 1% of the time on a
specific field. A second cheaper-model call (Haiku) just to confirm the
high-stakes fields (`policy_number`, `face_amount`) catches the long tail
without doubling cost on every field.

**Interview defense:**
> *Q: Source-grounding via substring match seems naive — what about paraphrased fields?*
> *A: It's intentionally naive for the fields where it applies — `policy_number`, dates, money, names. Those should appear verbatim in the source; if they don't, something's wrong. For paraphrased fields (`exclusions`, `coverage_limits`), I check that each list item appears at least partially. The validator doesn't try to ground `policy_type` or natural-language summaries — those are abstractive by design. The contract is: anything that should be verbatim is checked verbatim, anything that's summarization isn't checked at all but is constrained by the templated summary structure.*

---

## 3. Summarization

### 3.1 Extractive vs abstractive trade-offs

**Decision:** Templated abstractive. The summary follows a fixed Markdown
structure (`SUMMARY_SYSTEM_PROMPT` in `prompts.py`) with sections for Policy
Summary, Key Terms, Notable Exclusions & Conditions, and Things to Verify.
The LLM fills the slots; the template is constant.

**Trade-off table:**

| Approach | Quality on legalese | Hallucination risk | Reader experience | Verdict |
|---|---|---|---|---|
| Extractive (verbatim sentences) | Poor — policy legalese reads badly | Zero (it's source text) | Hard to scan; legal jargon | Reject |
| Pure abstractive (free-form LLM) | High | High | Inconsistent structure per doc | Reject |
| Templated abstractive (LLM + fixed sections) | High | Low (template + grounded fields) | Same structure every doc; broker can ctrl-F | Chosen |
| Citation-augmented (each fact links to source page) | High | Low | Best, but needs UI | Defer to M14 |

**Why for this exercise:** Policy documents are dense legalese. An extractive
summary reads like the original document, which defeats the point of having a
summary. A pure abstractive LLM call gives me a high-quality paragraph but
might miss the exclusions section entirely if the model decides they're
"unimportant." Templated abstractive gets the best of both: the fixed
structure forces the model to address each required section (Key Terms,
Exclusions, Things to Verify), and verbatim field substitution for IDs/dates/money
means the broker always sees the same numbers in the same place.

The template also forces a "Things to Verify" section which captures
ambiguity the LLM detected during extraction. For the Leland Stanford
sample, this includes the 20-day Free Look window (calendar-dependent) and
the premium curve over time.

**Production trade-off:** M14 (citation-augmented summaries) is the next
upgrade — every fact in the summary links to the source page where it
originated. The infrastructure for this is already in place: each extracted
field carries `source_page_hints` from the LLM. M14 just needs a UI to
surface them as inline links.

**Interview defense:**
> *Q: Why force a section structure on the LLM?*
> *A: Two reasons. (1) Brokers benefit from consistency — if "Notable Exclusions" is always section 3, they learn to scan there first. (2) Forcing sections is the cheapest way to prevent the LLM from omitting important categories. If I asked for "a summary" without structure, the LLM might write 200 words about coverage and skip exclusions on a doc where exclusions are short.*

### 3.2 Capturing critical policy details

**Decision:** Six mechanisms working together — each addresses a different
failure mode:

1. **Template forcing** — Key Terms, Notable Exclusions, Things to Verify sections are mandatory; the LLM can't skip a category.
2. **Verbatim field substitution** — policy number, dates, money, names are pulled from `extracted_fields` (already validated and grounded), not regenerated by the summary LLM. The summary prompt is given both the extracted fields and the full text and instructed to use the extracted values for the structured slots.
3. **Exclusions section is always present** — even if no exclusions were detected, the prompt instructs the LLM to write "(no other exclusions detected)" rather than omit the section.
4. **"Things to Verify" section** — forced category for ambiguity, missing fields, or unusual values the broker should confirm.
5. **Length constraint** (150-250 words) — long enough to be useful, short enough to read in 60 seconds, and short enough to discourage padding with non-critical detail.
6. **Broker-tuned definition of "critical"** — the system prompt explicitly identifies the audience as "an insurance brokerage analyst" so the model frames content for that reader.

**Why for this exercise:** "Critical" is audience-dependent — a regulator
cares about different fields than a broker who cares about different fields
than a policyholder. By specifying the audience in the prompt and forcing a
broker-relevant structure (Things to Verify, exclusions called out), I
optimize for one user persona explicitly rather than producing a generic
summary that serves no one well.

**Production trade-off:** The summary prompt is a single version (`v1.0`).
M8 (per-insurer prompts) and a per-LOB variant (life vs auto vs home) would
let me tune the definition of "critical" per document type — auto policies
need a different "Things to Verify" section than life policies.

**Interview defense:**
> *Q: What if the policy has 30 exclusions? Your summary only shows 2-4.*
> *A: The prompt asks for the most material exclusions — that's a judgment call I'm trusting the LLM to make under instruction. The exclusions list (full) is in `extracted_fields.exclusions_json`, and a future review UI (M7) would let brokers see the complete list. The summary is the "60-second briefing" view; the API response carries the structured data for everything else.*

### 3.3 Missing or ambiguous information

**Decision:** Conservative defaults — null over guess at the extraction layer,
explicit "Not specified" labels in the summary layer, and ambiguity surfaced
via `extraction_notes` and the Things to Verify section.

**Mechanisms:**

| Situation | Extraction behavior | Summary behavior |
|---|---|---|
| Field not present in document | Extracted as `null`; not a warning | Summary writes "Not specified in document" |
| Multiple candidate values | Picks the most likely; logs alternatives in `extraction_notes` | "Things to Verify" mentions the alternatives |
| Format ambiguous (e.g., date "05/06/07") | Best-effort parse; flag in `extraction_notes` | "Things to Verify" calls it out |
| Critical field missing (policy_number, insured_name) | `validate.py` adds an error-severity `ValidationWarning` | (no special handling; warning visible in API response) |
| Low confidence (< 0.7) | Field returned; warning logged | (no special handling; broker sees confidence in API) |

**Why for this exercise:** The cost of a wrong field is much higher than the
cost of a missing field — a broker can fill in a missing premium amount in
seconds, but a wrong premium amount might be carried into a quote, an
underwriting decision, or a customer-facing document. Conservative defaults
(null > guess) optimize for the right failure mode.

**Production trade-off:** Today, low-confidence fields are returned with a
warning but the API doesn't have a "review required" status. M7 (Web UI) would
surface a review queue where any document with error-severity warnings or
fields under 0.7 confidence goes to a human. Until we have that, the
recommendation is for the consumer to filter `warnings` and `confidence`
themselves.

**Interview defense:**
> *Q: How would you handle a fully unreadable document?*
> *A: Today: the pipeline still runs, the LLM returns mostly nulls, the validator adds error warnings for missing critical fields, and the summary writes "Not specified" for the empty slots. The doc is marked `completed` (extraction ran, just produced little) rather than `failed` (which is reserved for structural errors like bad PDF). M3 (OCR upgrade to Textract) would be the next investment for documents that are unreadable because of poor OCR rather than because the source is illegible.*

---

## 4. Data Architecture

### 4.1 Schema design

**Decision:** Five tables — `documents` (immutable), `processing_runs`
(append-only), `extracted_fields` (one per run), `summaries` (one per run),
`validation_warnings` (many per run). Designed around five principles:

1. **Immutable documents + versioned extraction** — the original PDF and its
   metadata never change; extraction can be re-run any number of times against
   the same document.
2. **Typed columns + raw JSON** — every important field (policy_number, dates,
   money) gets a typed column for query (`WHERE policy_number = X`), with the
   raw LLM JSON preserved in `raw_extraction_json` for forward compatibility.
   New fields can be added to the LLM schema without a DB migration.
3. **Per-field metadata as JSON** — `confidence_json` and
   `extraction_method_per_field_json` (regex vs llm origin) are stored as JSON
   blobs because they're per-run, per-field metadata that doesn't justify its
   own table.
4. **Append-only `processing_runs`** — `run_number` increments per document;
   never update in place. This is the versioning answer.
5. **Validation warnings as their own table** — not a JSON column on
   `extracted_fields`. Warnings need to be filtered, counted, and reported in
   isolation; a separate table makes those queries trivial.

**Trade-off table:**

| Decision | Alternative | Why I chose this |
|---|---|---|
| Typed + JSON | Pure JSON (NoSQL-style) | Typed columns enable indexed queries; raw JSON preserves forward compat |
| Typed + JSON | Pure typed (no JSON) | New LLM fields would require migrations; experimental fields can live in JSON without disruption |
| Append-only runs | Update extracted_fields in place | Append-only gives me an audit trail of prompt/model evolution for free |
| Warnings as table | Warnings in `extracted_fields.warnings_json` | Filterability — "show me all docs with error warnings on policy_number" is a SQL `WHERE` |
| One `summaries` row per run | Summary as a column on `extracted_fields` | Summaries get regenerated independently (M14) — separate row makes that cleaner |

**Why for this exercise:** Five tables is more than the brief minimum (which
would be one or two), but each addition pays for itself with a concrete query
pattern I want to support. The separation also makes the system inspectable: a
reviewer can `sqlite3 insurance.db` and immediately understand the data model
from the table names.

**Production trade-off:** In Postgres, `raw_extraction_json` would become
`JSONB` with a GIN index for partial queries (e.g., `WHERE raw_extraction_json
->> 'beneficiaries' IS NOT NULL`). In SQLite the JSON is opaque text, which
is fine for the MVP since I don't query into it.

**Interview defense:**
> *Q: Why not a document store like MongoDB?*
> *A: For the queries brokers actually run — "list policies expiring this month", "find policies for insured X" — relational with typed columns wins easily. For the queries I haven't thought of yet, JSONB in Postgres gives me document-store flexibility without giving up the relational model. NoSQL would optimize for a future I can't predict at the cost of a present I can.*

### 4.2 Linking extracted data back to source

**Decision:** Five levels of source linkage; the MVP ships levels 1 and 2,
levels 3-5 are roadmapped:

| Level | Granularity | MVP? | Implementation |
|---|---|---|---|
| L1: Document ID | "this extraction came from document `xyz`" | Yes | `extracted_fields.processing_run_id` → `processing_runs.document_id` |
| L2: Page hints | "field X came from pages [3, 5]" | Yes | `raw_extraction_json._source_page_hints` populated by LLM via tool schema |
| L3: Character offset | "field X is at chars 4521-4538" | No | Defer to M14 (needs UI) |
| L4: Bounding box | "field X is the rectangle (120, 340)-(280, 365) on page 3" | No | Defer to M14 (needs pdfplumber word coords) |
| L5: Inline citations | "in the summary, each fact links to its source page" | No | Defer to M14 |

**Why for this exercise:** L1 + L2 are cheap to ship and unblock the most
important production feature (the click-to-highlight UI). L3-L5 all require a
front-end to be useful — there's no point computing bounding boxes if no
component can render them. The L2 plumbing (LLM populates page hints; we
persist them; the validator weights them in confidence) means L3-L5 can be
added incrementally without changing the schema.

The validator already exploits L2: a field that's source-grounded AND has a
page hint gets confidence 0.95, while a grounded field without page hint gets
0.85. This is a small but meaningful signal — if the LLM was confident enough
to cite a page, that's evidence it actually saw the value.

**Production trade-off:** M14 (source-grounded UI) is where L3-L5 land. The
PDF viewer highlights the bounding box for any clicked field. This is high-UX
value but requires (1) a web UI (M7), (2) bounding-box extraction in
`pdf.py`, and (3) a per-field offset column in `extracted_fields`. All
incremental on top of what we have.

**Interview defense:**
> *Q: Page hints come from the LLM — what if they're wrong?*
> *A: They sometimes are — the LLM occasionally cites the wrong page. That's why source-grounding is independent of page hints: a value with a wrong page hint but right text still grounds (and we'd see this as "grounded but no page hint match" if we measured it). For production, M14 would re-verify page hints by re-locating the grounded value in the cited page's text. Today, the validator treats page hint presence as a confidence signal, not as proof.*

### 4.3 Versioning strategy for re-processed documents

**Decision:** Append-only `processing_runs`. Each `/reprocess` adds a new row
with `run_number = max + 1` against the same `document_id`. The PDF, document
metadata, and `pdf_sha256` are unchanged; only the extraction is new.

**Query patterns I support:**

| Query | SQL |
|---|---|
| Current extraction | `SELECT * FROM processing_runs WHERE document_id = ? ORDER BY run_number DESC LIMIT 1` |
| All extractions for a document | `SELECT * FROM processing_runs WHERE document_id = ? ORDER BY run_number` |
| Diff between two runs | App-layer compare of two `ExtractedFields` rows |
| Audit: which model/prompt was used when | `SELECT run_number, model_id, extraction_prompt_version, started_at FROM processing_runs WHERE document_id = ?` |

**Why for this exercise:** The brief explicitly asks about versioning. The
cheapest answer that actually works is append-only with a `run_number`. This
gives me:
- A complete history of every extraction attempt against a document.
- The ability to A/B prompts in production (run prompt v2 against historical docs and compare against v1's results).
- A clean audit trail (which model produced which fields when).
- No mutation of historical data, so reviewers can trust that an old run's fields are what we extracted at that time.

Update-in-place was the alternative — simpler schema, no history. I rejected
it because losing history makes prompt iteration unsafe: I can't tell whether
a new prompt is better without an old extraction to compare against.

**Trade-off table:**

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| Append-only `processing_runs` | Full history; safe iteration; cheap audit | Storage grows linearly with reprocesses | Chosen |
| Update-in-place on `extracted_fields` | Single source of truth per doc; no JOIN | Loses history; can't compare prompt versions | Rejected |
| Event-sourced (every change is an event) | Maximal flexibility; perfect replay | Overengineered for an MVP | Defer until needed |

**Production trade-off:** At 1M documents × 10 reprocesses each = 10M rows of
`processing_runs` + 10M rows of `extracted_fields`. Still small (~GB in
Postgres), but I'd add a cold-storage policy where runs older than the last
3 per document move to a separate `processing_runs_archive` table. This
keeps hot queries fast without losing the audit trail.

**Interview defense:**
> *Q: What if a reprocess produces worse results than the previous run?*
> *A: That's exactly why I keep history. The `/reprocess` endpoint adds a new run; it doesn't replace the previous one. The "current" extraction is the latest run by default, but the API could expose `?run=N` to read any historical run (today the read path always returns the latest; the schema supports per-run queries). A worse reprocess is a reversible decision — we could ship a "promote run N to current" feature without schema changes.*

---

## What I'd Build Next

The post-MVP roadmap lives in the plan document; the top 9 milestones I'd
ship in the first 6 months post-launch:

| Tier | ID | Milestone | Why |
|---|---|---|---|
| P0 | M1 | Auth + RBAC | Required before any real user |
| P0 | M2 | Encryption at rest + audit log | Compliance pre-launch |
| P0 | M3 | OCR upgrade to AWS Textract or Azure DI | Tesseract accuracy gap on real scans |
| P0 | M4 | Eval harness expansion (5 → 50+ PDFs) | First prompt regression hits prod |
| P1 | M5 | Async processing (Celery + Redis) | Crosses 10 docs/min or 30-page docs |
| P1 | M6 | Postgres + S3 migration | Multi-instance deployment |
| P1 | M7 | Web upload + review UI | Broker corrections create training data |
| P1 | M8 | Per-insurer prompt overrides | Top insurers show systematic errors |
| P1 | M9 | Webhook notifications | Broker CRM integration |

M10-M22 (Tier P2/P3 — Haiku, prompt caching, RAG, vision LLM, i18n, etc.)
are tracked but situational; each has a defined trigger condition for
promotion to P1.
