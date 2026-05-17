"""Streamlit frontend for insurance-summarizer.

Usage:
    streamlit run frontend/app.py

Env vars:
    BACKEND_URL   FastAPI backend URL (default: http://localhost:8000)
"""
from __future__ import annotations
import os
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


def render_extracted_fields(result: dict) -> None:
    """2-column layout of extracted fields with confidence bars."""
    fields = result.get("extracted_fields") or {}
    confidence = result.get("confidence") or {}
    origin = result.get("extraction_method_per_field") or {}

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
            st.markdown(f"**{label}**")
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


def render_warnings(warnings: list[dict]) -> None:
    if not warnings:
        return
    for w in warnings:
        severity = w.get("severity", "info")
        field = w.get("field") or "(document-level)"
        message = w.get("message", "")
        line = f"**{field}** — {message}"
        if severity == "error":
            st.error(line)
        elif severity == "warning":
            st.warning(line)
        else:
            st.info(line)


# === Sidebar ===
with st.sidebar:
    st.title("Insurance Summarizer")
    st.caption("v2 demo — Streamlit frontend")

    backend_input = st.text_input("Backend URL", value=BACKEND_URL, key="backend_url_input")
    if backend_input != BACKEND_URL:
        BACKEND_URL = backend_input

    health = fetch_health()
    if health:
        st.success(f"Backend OK — model: `{health.get('model', '?')}` · env: `{health.get('environment', '?')}`")
    else:
        st.error(f"Backend unreachable at {BACKEND_URL}")

    st.divider()
    st.subheader("Recent uploads")
    recent = list_documents(limit=5)
    if recent:
        for doc in recent:
            st.caption(f"`{doc.get('id', '')[:8]}` · {doc.get('filename', '?')} · {doc.get('status', '?')}")
    else:
        st.caption("(none yet)")


# === Main panel ===
st.title("Upload an insurance policy PDF")
st.caption(
    "Extracts structured fields via hybrid regex + LLM pipeline; produces a "
    "human-readable summary. Source-grounded validation flags possible "
    "hallucinations as warnings."
)

uploaded = st.file_uploader("Choose a PDF", type=["pdf"], accept_multiple_files=False)

if uploaded is not None:
    with st.spinner(f"Processing `{uploaded.name}` ... (~10-30s)"):
        try:
            result = upload_document(uploaded.getvalue(), uploaded.name)
        except httpx.HTTPStatusError as e:
            st.error(f"Backend error: {e.response.status_code} — {e.response.text}")
            st.stop()
        except Exception as e:
            st.error(f"Upload failed: {type(e).__name__} — {e}")
            st.stop()

    st.success(f"Done · document id `{result.get('id', '?')[:12]}` · status `{result.get('status')}`")

    tab_fields, tab_summary, tab_warnings, tab_raw = st.tabs(
        ["Extracted Fields", "Summary", "Warnings", "Raw JSON"]
    )

    with tab_fields:
        render_extracted_fields(result)

    with tab_summary:
        summary = result.get("summary_markdown")
        if summary:
            st.markdown(summary)
        else:
            st.info("No summary generated (LLM summary call may have failed — check warnings tab).")

    with tab_warnings:
        warnings = result.get("warnings") or []
        if not warnings:
            st.success("No validation warnings.")
        else:
            render_warnings(warnings)

    with tab_raw:
        st.json(result)

# Footer
st.divider()
st.caption(
    f"Backend: {BACKEND_URL} · "
    f"Run locally: `streamlit run frontend/app.py` · "
    f"Backend: `uvicorn app.main:app --port 8000`"
)
