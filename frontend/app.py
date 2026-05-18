"""Streamlit frontend for insurance-summarizer.

Usage:
    streamlit run frontend/app.py

Env vars:
    BACKEND_URL   FastAPI backend URL (default: http://localhost:8000)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = 60.0  # seconds — extraction can take a while


# Page config
st.set_page_config(
    page_title="Insurance Document Summarizer",
    page_icon=":page_facing_up:",
    layout="wide",
)


def fetch_health() -> dict | None:
    try:
        r = httpx.get(f"{BACKEND_URL}/healthz", timeout=5.0)
        return r.json()
    except Exception:
        return None


def upload_document(file_bytes: bytes, filename: str) -> dict:
    """POST PDF to backend, return parsed DocumentResponse dict."""
    files = {"file": (filename, file_bytes, "application/pdf")}
    r = httpx.post(f"{BACKEND_URL}/documents", files=files, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.json()


def list_documents(limit: int = 10) -> list[dict]:
    try:
        r = httpx.get(f"{BACKEND_URL}/documents", params={"limit": limit}, timeout=5.0)
        return r.json().get("items", [])
    except Exception:
        return []


def fetch_pdf_bytes(doc_id: str) -> bytes | None:
    """Fetch raw PDF bytes for a previously-uploaded document.

    Returns None if the fetch fails (e.g. backend offline, PDF expired).
    """
    try:
        r = httpx.get(f"{BACKEND_URL}/documents/{doc_id}/pdf", timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def confidence_color(score: float) -> str:
    """Return a CSS color hex for confidence bar."""
    if score >= 0.85:
        return "#22c55e"   # green
    if score >= 0.55:
        return "#eab308"   # yellow
    return "#ef4444"       # red


def render_confidence_bar(score: float, label: str = "") -> None:
    """Render a horizontal confidence bar via HTML."""
    color = confidence_color(score)
    width_pct = int(score * 100)
    bar_html = f"""
    <div style="background:#e5e7eb; border-radius:4px; height:18px; width:100%; position:relative;">
      <div style="background:{color}; width:{width_pct}%; height:100%; border-radius:4px;"></div>
      <div style="position:absolute; top:0; left:8px; color:#000; font-size:12px; line-height:18px; font-weight:600;">
        {score:.0%}{' · ' + label if label else ''}
      </div>
    </div>
    """
    st.markdown(bar_html, unsafe_allow_html=True)


def render_page_badge(pages: list[int] | None) -> str:
    """Return inline HTML for the small page-citation badge.

    Empty string if no pages — caller can concatenate unconditionally.
    """
    if not pages:
        return ""
    # De-duplicate while preserving order
    seen = set()
    unique_pages: list[int] = []
    for p in pages:
        if p not in seen:
            seen.add(p)
            unique_pages.append(p)
    label = ", ".join(f"p.{p}" for p in unique_pages)
    return (
        f'<span style="font-size:11px; color:#6b7280; margin-left:8px;">'
        f'\U0001F4C4 {label}'
        f'</span>'
    )


def render_extracted_fields(result: dict) -> None:
    """2-column layout of extracted fields with confidence bars + page badges."""
    fields = result.get("extracted_fields") or {}
    confidence = result.get("confidence") or {}
    origin = result.get("extraction_method_per_field") or {}
    page_hints = result.get("source_page_hints") or {}

    if not fields:
        st.warning("No fields extracted.")
        return

    # Render in a structured way
    display_fields = [
        ("Policy Number", "policy_number"),
        ("Insured Name", "insured_name"),
        ("Insurer Name", "insurer_name"),
        ("Policy Type", "policy_type"),
        ("Effective Date", "effective_date"),
        ("Expiration Date", "expiration_date"),
        ("Premium Amount", "premium_amount"),
        ("Premium Frequency", "premium_frequency"),
        ("Face Amount", "face_amount"),
    ]

    col1, col2 = st.columns(2)
    for i, (label, key) in enumerate(display_fields):
        col = col1 if i % 2 == 0 else col2
        with col:
            value = fields.get(key)
            conf = confidence.get(key, 0.0)
            method = origin.get(key, "—")
            badge = render_page_badge(page_hints.get(key))
            # B3: inline page badge next to label (rendered as raw HTML so the
            # styled span survives).
            st.markdown(f"**{label}**{badge}", unsafe_allow_html=True)
            if value is not None and value != "":
                st.markdown(f"`{value}`")
                render_confidence_bar(conf, label=f"via {method}")
            else:
                st.markdown("_Not extracted_")
            st.markdown("")  # spacer

    # Lists (exclusions, coverage)
    exclusions = fields.get("exclusions") or []
    if exclusions:
        st.markdown("**Exclusions**")
        for e in exclusions:
            st.markdown(f"- {e}")

    coverage = fields.get("coverage_limits") or []
    if coverage:
        st.markdown("**Coverage Limits**")
        for c in coverage:
            name = c.get("name", "—")
            amount = c.get("limit_amount")
            st.markdown(f"- {name}: `{amount}` {c.get('currency', 'USD')}")


# Warning classifier — turns raw ValidationWarning messages into
# reviewer-friendly cards. Patterns matched against the lowercased message;
# first match wins. Keep messages here in sync with app/validate.py and
# the warning-emitting sites in app/pipeline.py / app/routes.py.
_WARNING_RULES = [
    {
        "match": "not found verbatim",
        "title": "Couldn't verify in source",
        "explanation": (
            "The LLM returned this value, but the system couldn't find it "
            "word-for-word in the PDF text. Common causes: the LLM normalized a "
            "format (e.g. `$` → `USD`, `May 1, 2038` → `2038-05-01`) or "
            "paraphrased the original."
        ),
        "action": (
            "Open the PDF Preview tab and verify the value yourself. Accept if "
            "it's a format change; flag if the meaning shifted."
        ),
    },
    {
        "match": "cross-model disagreement",
        "title": "Two LLMs disagree",
        "explanation": (
            "The primary and secondary LLM providers extracted different values "
            "for this field. One of them is wrong."
        ),
        "action": (
            "Check the PDF directly — this is the highest-signal warning the "
            "system produces."
        ),
    },
    {
        "match": "critical field",
        "title": "Critical field missing",
        "explanation": (
            "Neither regex nor LLM extracted this required field. The document "
            "may use unusual field labels, be poorly OCR'd, or genuinely not "
            "contain this field."
        ),
        "action": (
            "Inspect the PDF Preview tab to confirm. If the field is present "
            "but unrecognized, the prompt or regex pattern needs to learn it."
        ),
    },
    {
        "match": "is not before expiration",
        "title": "Date order inverted",
        "explanation": (
            "The effective date is on or after the expiration date — extraction "
            "is wrong."
        ),
        "action": (
            "Re-upload and consider switching LLM provider, or check the PDF "
            "for unusual date layout."
        ),
    },
    {
        "match": "is not valid iso 8601",
        "title": "Date format unusual",
        "explanation": (
            "The LLM returned a date string that doesn't parse as ISO 8601 "
            "(YYYY-MM-DD)."
        ),
        "action": "Likely a minor formatting issue; verify on the PDF.",
    },
    {
        "match": "must be > 0",
        "title": "Money value not positive",
        "explanation": (
            "Amount is zero or negative — almost certainly an extraction error."
        ),
        "action": (
            "Re-upload; consider regex pattern adjustment if it persists."
        ),
    },
    {
        "match": "suspiciously large",
        "title": "Money value abnormally large",
        "explanation": (
            "Value exceeds $1B — likely the LLM mis-parsed a column or misread "
            "a multiplier."
        ),
        "action": "Compare against PDF; almost always wrong on consumer policies.",
    },
    {
        "match": "not numeric",
        "title": "Money value not a number",
        "explanation": (
            "The value should be numeric but came back as a string or other "
            "type."
        ),
        "action": "Re-upload; underlying LLM may need a stricter prompt.",
    },
    {
        "match": "unusually short (<",
        "title": "Policy number unusually short",
        "explanation": (
            "Policy numbers under 4 characters are rare — could be a partial "
            "extraction."
        ),
        "action": "Verify on PDF; accept if it really is that short.",
    },
    {
        "match": "not in standard enum",
        "title": "Non-standard premium frequency",
        "explanation": (
            "The value isn't one of the expected enum entries "
            "(monthly/quarterly/etc.)."
        ),
        "action": "Usually safe to accept; standardize downstream if needed.",
    },
    {
        "match": "policy duration is unusually short",
        "title": "Short policy duration",
        "explanation": (
            "The policy is shorter than 30 days — unusual unless it's a binder "
            "or short-term cover."
        ),
        "action": "Confirm the policy type. Accept for short-term/binder policies.",
    },
    {
        "match": "> 10% of face amount",
        "title": "Premium-to-face ratio looks off",
        "explanation": (
            "Annual premium exceeds 10% of face amount. Normal for short-term "
            "or investment-linked products, unusual for term life."
        ),
        "action": "Verify product type. Accept if it's a non-protection product.",
    },
    {
        "match": "cross-model verification skipped",
        "title": "Cross-model verifier didn't run",
        "explanation": (
            "Secondary LLM provider isn't configured (only one API key set)."
        ),
        "action": (
            "Informational only. Set the second `*_API_KEY` env var to enable."
        ),
    },
    {
        "match": "validation skipped",
        "title": "Validation step errored",
        "explanation": (
            "Validation logic threw an exception. Confidence scores may be "
            "missing."
        ),
        "action": "Engineer should check the server log.",
    },
    {
        "match": "summary generation failed",
        "title": "Summary couldn't be generated",
        "explanation": (
            "Extraction succeeded but the summary LLM call failed (rate limit, "
            "content filter, etc.)."
        ),
        "action": "Try re-uploading. Extracted Fields tab is still valid.",
    },
]


def _classify_warning(w: dict) -> dict:
    msg = (w.get("message") or "").lower()
    for rule in _WARNING_RULES:
        if rule["match"] in msg:
            return rule
    return {
        "title": "Warning",
        "explanation": (
            "An issue was detected but the UI doesn't have a friendly "
            "description for it yet. See raw message below."
        ),
        "action": "",
    }


def _prettify_field(field: str | None) -> str:
    if not field:
        return "Document-level"
    return field.replace("_", " ").title()


_SEVERITY_ICONS = {"error": "🔴", "warning": "🟡", "info": "🟢"}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def render_warnings(warnings: list[dict]) -> None:
    if not warnings:
        st.success(
            "No validation warnings — every field is grounded in source text."
        )
        return

    warnings = sorted(
        warnings, key=lambda w: _SEVERITY_ORDER.get(w.get("severity"), 3)
    )

    error_n = sum(1 for w in warnings if w.get("severity") == "error")
    warn_n = sum(1 for w in warnings if w.get("severity") == "warning")
    info_n = sum(1 for w in warnings if w.get("severity") == "info")

    summary_parts = []
    if error_n:
        summary_parts.append(f"🔴 **{error_n}** need immediate attention")
    if warn_n:
        summary_parts.append(f"🟡 **{warn_n}** for human review")
    if info_n:
        summary_parts.append(f"🟢 **{info_n}** informational")
    if summary_parts:
        st.markdown("**Summary:** " + " · ".join(summary_parts))
        st.caption(
            "Each card explains what happened and recommends an action. The "
            "raw system message is in the expander on each card."
        )
        st.divider()

    for w in warnings:
        sev = w.get("severity", "info")
        icon = _SEVERITY_ICONS.get(sev, "ℹ️")
        field_label = _prettify_field(w.get("field"))
        card = _classify_warning(w)

        with st.container(border=True):
            st.markdown(f"### {icon} {field_label} — {card['title']}")
            st.markdown(card["explanation"])
            if card["action"]:
                st.markdown(f"**What to do:** {card['action']}")
            with st.expander("Show raw system message"):
                st.code(w.get("message", ""), language="text")


def render_pdf_preview(doc_id: str, pdf_bytes: bytes | None) -> None:
    """B2: render the source PDF inline.

    Tries `streamlit-pdf-viewer` first; falls back to a base64 iframe so the
    tab is still useful when the package isn't installed.
    """
    if not pdf_bytes:
        st.warning(
            f"Could not fetch PDF bytes from {BACKEND_URL}/documents/{doc_id}/pdf."
        )
        return
    try:
        from streamlit_pdf_viewer import pdf_viewer  # type: ignore

        pdf_viewer(pdf_bytes, width=700)
    except ImportError:
        b64 = base64.b64encode(pdf_bytes).decode()
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{b64}" '
            f'width="100%" height="800"></iframe>',
            unsafe_allow_html=True,
        )


def _summarize_for_batch(result: dict) -> dict[str, Any]:
    """Pluck a few fields out for the batch summary table."""
    fields = result.get("extracted_fields") or {}
    warnings = result.get("warnings") or []
    return {
        "Doc ID": (result.get("id") or "")[:12],
        "Status": result.get("status", "?"),
        "Policy #": fields.get("policy_number") or "—",
        "Premium": fields.get("premium_amount") or "—",
        "Warnings": len(warnings),
    }


def render_single_result(result: dict) -> None:
    """5-tab layout for a single successfully-uploaded document."""
    doc_id = result.get("id", "")
    st.success(
        f"Done · document id `{doc_id[:12]}` · status `{result.get('status')}`"
    )

    # B2: "PDF Preview" tab is inserted BEFORE "Raw JSON" (now the 5th tab).
    tab_fields, tab_summary, tab_warnings, tab_pdf, tab_raw = st.tabs(
        ["Extracted Fields", "Summary", "Warnings", "PDF Preview", "Raw JSON"]
    )

    with tab_fields:
        render_extracted_fields(result)

    with tab_summary:
        summary = result.get("summary_markdown")
        if summary:
            st.markdown(summary)
        else:
            st.info(
                "No summary generated (LLM summary call may have failed — "
                "check warnings tab)."
            )

    with tab_warnings:
        warnings = result.get("warnings") or []
        if not warnings:
            st.success("No validation warnings.")
        else:
            render_warnings(warnings)

    with tab_pdf:
        # Streamlit re-runs the script on every interaction, so fetching here
        # is fine — it only fires while this tab is "active enough" for the
        # render to happen.
        pdf_bytes = fetch_pdf_bytes(doc_id) if doc_id else None
        render_pdf_preview(doc_id, pdf_bytes)

    with tab_raw:
        st.json(result)


# === Sidebar ===
with st.sidebar:
    st.title("Insurance Summarizer")
    st.caption("v3 demo — Streamlit frontend")

    backend_input = st.text_input(
        "Backend URL", value=BACKEND_URL, key="backend_url_input"
    )
    if backend_input != BACKEND_URL:
        BACKEND_URL = backend_input

    health = fetch_health()
    if health:
        st.success(
            f"Backend OK — model: `{health.get('model', '?')}` · "
            f"env: `{health.get('environment', '?')}`"
        )
    else:
        st.error(f"Backend unreachable at {BACKEND_URL}")

    st.divider()
    st.subheader("Recent uploads")
    recent = list_documents(limit=5)
    if recent:
        for doc in recent:
            st.caption(
                f"`{doc.get('id', '')[:8]}` · {doc.get('filename', '?')} · "
                f"{doc.get('status', '?')}"
            )
    else:
        st.caption("(none yet)")


# === Main panel ===
st.title("Upload one or more insurance policy PDFs")
st.caption(
    "Extracts structured fields via hybrid regex + LLM pipeline; produces a "
    "human-readable summary. Source-grounded validation flags possible "
    "hallucinations as warnings. Upload multiple files for batch mode."
)

# B4: batch upload — accept_multiple_files=True. A single file falls through to
# the existing rich single-file flow; multiple files trigger the batch loop.
uploaded_files = st.file_uploader(
    "Choose one or more PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    if len(uploaded_files) == 1:
        single = uploaded_files[0]
        with st.spinner(f"Processing `{single.name}` ... (~10-30s)"):
            try:
                result = upload_document(single.getvalue(), single.name)
            except httpx.HTTPStatusError as e:
                st.error(
                    f"Backend error: {e.response.status_code} — {e.response.text}"
                )
                st.stop()
            except Exception as e:
                st.error(f"Upload failed: {type(e).__name__} — {e}")
                st.stop()
        render_single_result(result)

    else:
        # Multi-file batch flow — per-file progress + summary table.
        # Errors per file are collected, NOT raised — the loop runs to completion.
        st.info(
            f"Batch mode: processing {len(uploaded_files)} files sequentially."
        )

        progress = st.progress(0.0, text="Starting batch...")
        rows: list[dict[str, Any]] = []
        successes: list[dict] = []
        failures: list[tuple[str, str]] = []

        for idx, up in enumerate(uploaded_files, start=1):
            progress.progress(
                (idx - 1) / len(uploaded_files),
                text=f"({idx}/{len(uploaded_files)}) {up.name}",
            )
            try:
                result = upload_document(up.getvalue(), up.name)
                rows.append(
                    {"Filename": up.name, **_summarize_for_batch(result)}
                )
                successes.append(result)
            except httpx.HTTPStatusError as e:
                failures.append(
                    (
                        up.name,
                        f"HTTP {e.response.status_code}: {e.response.text[:120]}",
                    )
                )
                rows.append(
                    {
                        "Filename": up.name,
                        "Doc ID": "—",
                        "Status": "failed",
                        "Policy #": "—",
                        "Premium": "—",
                        "Warnings": 0,
                    }
                )
            except Exception as e:
                failures.append((up.name, f"{type(e).__name__}: {e}"))
                rows.append(
                    {
                        "Filename": up.name,
                        "Doc ID": "—",
                        "Status": "failed",
                        "Policy #": "—",
                        "Premium": "—",
                        "Warnings": 0,
                    }
                )

        progress.progress(
            1.0,
            text=f"Done — {len(successes)}/{len(uploaded_files)} succeeded",
        )

        st.subheader("Batch summary")
        st.table(rows)

        if failures:
            st.subheader(f"Errors ({len(failures)})")
            for name, err in failures:
                st.error(f"**{name}** — {err}")

        # Per-file detail expander for everything that succeeded
        if successes:
            st.subheader("Per-file details")
            for result in successes:
                with st.expander(
                    f"{result.get('filename', '?')} · "
                    f"`{(result.get('id') or '')[:12]}`"
                ):
                    render_single_result(result)

# Footer
st.divider()
st.caption(
    f"Backend: {BACKEND_URL} · "
    f"Run locally: `streamlit run frontend/app.py` · "
    f"Backend: `uvicorn app.main:app --port 8000`"
)
