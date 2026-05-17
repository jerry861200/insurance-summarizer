# Streamlit Frontend

Upload one or more insurance PDFs, see extracted fields with per-field
confidence bars, page-level source citations, the Markdown summary, validation
warnings, and an inline preview of the source PDF.

## Quick start

```bash
# Backend (in one terminal)
cd insurance-summarizer
source venv/bin/activate
uvicorn app.main:app --port 8000 --reload

# Frontend (in another terminal)
streamlit run frontend/app.py
# Opens http://localhost:8501
```

## Pointing at a deployed backend

```bash
BACKEND_URL=https://insurance-summarizer.up.railway.app streamlit run frontend/app.py
```

## What you see

- **Sidebar:** backend health, recent uploads.
- **Single-file mode (one PDF uploaded):** 5-tab layout
  - **Extracted Fields:** policy number, insured, dates, amounts — each with a
    colored confidence bar (green >= 85%, yellow 55-85%, red <55%) and a
    `via regex/llm` annotation showing the extraction origin. When the backend
    returns `source_page_hints`, each field is decorated with a small
    "page" badge citing the source page(s) — see B3 below.
  - **Summary:** the Markdown summary.
  - **Warnings:** validation warnings (info/warning/error severity).
  - **PDF Preview:** the source PDF rendered inline — see B2 below.
  - **Raw JSON:** the full backend response for debugging.
- **Batch mode (multiple PDFs uploaded):**
  - Files are processed sequentially with a progress bar.
  - Errors per file are collected (the loop never aborts).
  - A summary table shows `Filename / Doc ID / Status / Policy # / Premium / Warnings`.
  - Per-file expanders below the table contain the full 5-tab detail view.

## What's new in v3

- **B2 — PDF Preview tab.** The source PDF is fetched from
  `GET /documents/{id}/pdf` and rendered inline. Uses
  [`streamlit-pdf-viewer`](https://pypi.org/project/streamlit-pdf-viewer/)
  if it is installed; otherwise falls back to a base64-encoded `<iframe>` so
  the tab still works without the optional dependency.
- **B3 — Page-citation badges.** Next to each extracted field, a small
  "page" badge (e.g. `p.1, p.3`) cites the page(s) where the LLM saw evidence
  for that value (sourced from `result["source_page_hints"][field]`). Fields
  without hints render no badge.
- **B4 — Batch upload.** The file uploader now accepts multiple PDFs. With one
  file the existing single-file flow is unchanged; with several files the app
  switches to batch mode (per-file progress, summary table, per-file detail
  expanders). Errors per file are collected and displayed, not raised.

## Notes on the backend bug fix that landed alongside this work (B1)

Prior to v3, `DocumentResponse.confidence`, `extraction_method_per_field`, and
`warnings` were declared on the schema but never populated by
`_document_to_response`. The frontend already read them and silently showed
`0%` confidence bars + an empty warnings tab. v3 wires those fields up
(reading `confidence_json`, `extraction_method_per_field_json`, and the
`ProcessingRun.validation_warnings` relationship), and also surfaces a new
`source_page_hints: dict[str, list[int]]` field used by B3.
