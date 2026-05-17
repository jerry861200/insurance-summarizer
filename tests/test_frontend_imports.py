"""Smoke test: ensure the Streamlit frontend module imports without error.

This is the cheapest possible regression guard for the frontend — catches
syntax errors, missing/renamed imports, and top-level NameErrors before they
hit a human running `streamlit run`. The Streamlit runtime quirks (script
re-execution, session state) are not exercised here — the goal is "the file
loads" only.
"""

from __future__ import annotations


def test_frontend_imports_cleanly():
    import importlib

    # If this raises, the frontend has a syntax or import error.
    importlib.import_module("frontend.app")
