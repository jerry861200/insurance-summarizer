"""End-to-end API tests using FastAPI TestClient + mock LLM provider.

All routes are exercised against the in-memory SQLite session from `tmp_db`.
Tests that depend on routes that haven't been built yet (list/delete/pdf/
reprocess) are individually skipped rather than failing the whole module.
"""

from __future__ import annotations

import pytest

try:
    from app.main import app  # noqa: F401
    from app.models import ProcessingRun
    from app.routes import router  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("app.routes / app.main not built yet", allow_module_level=True)


def _route_exists(client, method: str, path_template: str) -> bool:
    """Inspect FastAPI routes to detect whether a given endpoint is registered."""
    method = method.upper()
    for route in client.app.routes:
        # Compare on .path (template like "/documents/{document_id}")
        if getattr(route, "path", None) == path_template and method in getattr(
            route, "methods", set()
        ):
            return True
    return False


# -------------------- /healthz --------------------


def test_healthz_returns_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


# -------------------- POST /documents --------------------


def test_upload_pdf_returns_201_with_extracted_fields(client, sample_pdf_bytes):
    files = {"file": ("leland_stanford_policy.pdf", sample_pdf_bytes, "application/pdf")}
    resp = client.post("/documents", files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["extracted_fields"]["policy_number"] == "VF99999990"
    assert body["status"] == "completed"
    # The mock LLM should have been hit at least once.
    assert client.mock_llm.extract_calls >= 1

    # B1: response now populates previously-silent-empty fields
    assert isinstance(body["confidence"], dict)
    assert body["confidence"] is not None
    assert isinstance(body["source_page_hints"], dict)
    assert body["source_page_hints"] is not None
    assert isinstance(body["extraction_method_per_field"], dict)
    # MockLLMProvider sets source_page_hints={"policy_number": [1], "effective_date": [1]}
    assert body["source_page_hints"].get("policy_number") == [1]
    assert body["source_page_hints"].get("effective_date") == [1]


def test_upload_rejects_non_pdf_filename(client):
    files = {"file": ("notes.txt", b"hello", "text/plain")}
    resp = client.post("/documents", files=files)
    assert resp.status_code == 400


# -------------------- GET /documents/{id} --------------------


def test_get_document_returns_existing(client, sample_pdf_bytes):
    files = {"file": ("policy.pdf", sample_pdf_bytes, "application/pdf")}
    upload = client.post("/documents", files=files)
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == doc_id
    assert body["extracted_fields"]["policy_number"] == "VF99999990"
    # B1: GET also returns populated source_page_hints/confidence (not None)
    assert isinstance(body["source_page_hints"], dict)
    assert isinstance(body["confidence"], dict)
    assert body["source_page_hints"].get("policy_number") == [1]


def test_get_document_404_for_unknown_id(client):
    resp = client.get("/documents/does-not-exist")
    assert resp.status_code == 404


# -------------------- GET /documents (list) --------------------


def test_list_documents_includes_uploaded_doc(client, sample_pdf_bytes):
    if not _route_exists(client, "GET", "/documents"):
        pytest.skip("GET /documents not implemented yet")

    files = {"file": ("policy.pdf", sample_pdf_bytes, "application/pdf")}
    upload = client.post("/documents", files=files)
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    resp = client.get("/documents")
    assert resp.status_code == 200
    items = resp.json()
    # Accept either a bare list or a paginated wrapper.
    if isinstance(items, dict):
        items = items.get("items") or items.get("documents") or []
    ids = [item.get("id") for item in items]
    assert doc_id in ids


# -------------------- GET /documents/{id}/pdf --------------------


def test_get_document_pdf_returns_pdf_bytes(client, sample_pdf_bytes):
    if not _route_exists(client, "GET", "/documents/{document_id}/pdf"):
        pytest.skip("GET /documents/{document_id}/pdf not implemented yet")

    files = {"file": ("policy.pdf", sample_pdf_bytes, "application/pdf")}
    upload = client.post("/documents", files=files)
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    resp = client.get(f"/documents/{doc_id}/pdf")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").lower().startswith("application/pdf")
    assert resp.content.startswith(b"%PDF")


# -------------------- POST /documents/{id}/reprocess --------------------


def test_reprocess_creates_new_processing_run(client, tmp_db, sample_pdf_bytes):
    if not _route_exists(client, "POST", "/documents/{document_id}/reprocess"):
        pytest.skip("POST /documents/{document_id}/reprocess not implemented yet")

    files = {"file": ("policy.pdf", sample_pdf_bytes, "application/pdf")}
    upload = client.post("/documents", files=files)
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    runs_before = (
        tmp_db.query(ProcessingRun).filter(ProcessingRun.document_id == doc_id).count()
    )

    resp = client.post(f"/documents/{doc_id}/reprocess")
    assert resp.status_code in (200, 201, 202), resp.text

    tmp_db.expire_all()
    runs_after = (
        tmp_db.query(ProcessingRun).filter(ProcessingRun.document_id == doc_id).count()
    )
    assert runs_after == runs_before + 1, (
        f"Expected a new ProcessingRun after reprocess, got {runs_before}->{runs_after}"
    )


# -------------------- DELETE /documents/{id} --------------------


def test_delete_document_then_get_returns_404(client, sample_pdf_bytes):
    if not _route_exists(client, "DELETE", "/documents/{document_id}"):
        pytest.skip("DELETE /documents/{document_id} not implemented yet")

    files = {"file": ("policy.pdf", sample_pdf_bytes, "application/pdf")}
    upload = client.post("/documents", files=files)
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    resp = client.delete(f"/documents/{doc_id}")
    assert resp.status_code in (200, 204)

    follow = client.get(f"/documents/{doc_id}")
    assert follow.status_code == 404
