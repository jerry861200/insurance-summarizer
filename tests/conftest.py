"""Shared fixtures for the test suite.

Highlights:
- `tmp_db` swaps `app.storage` to an in-memory SQLite engine for the duration
  of the test (and points the pipeline's `save_pdf_bytes` at a tmp dir so we
  don't litter the repo with stray PDF files).
- `mock_llm_provider` is a deterministic `LLMProvider`-compatible stub that
  returns the Leland Stanford expected values. Tests should NEVER hit the real
  Anthropic API.
- `client` wires the above into a FastAPI TestClient and overrides
  `get_db` + `_get_llm` so handlers see the temp DB and stub LLM.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Import top-level so collection fails loudly if app skeleton is missing.
from app import storage as storage_module
from app.extractors.llm import ExtractionResult, SummaryResult
from app.storage import Base


# Path to the bundled sample policy used across multiple test files.
SAMPLE_PDF = Path(__file__).resolve().parent.parent / "sample" / "leland_stanford_policy.pdf"


# -------------------- Mock LLM provider --------------------


class MockLLMProvider:
    """Deterministic LLMProvider stub. Implements the `LLMProvider` Protocol
    (extract_fields + summarize) without touching Anthropic."""

    def __init__(self) -> None:
        self.extract_calls = 0
        self.summarize_calls = 0

    def extract_fields(self, full_text: str) -> ExtractionResult:
        self.extract_calls += 1
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
                "coverage_limits": [
                    {"name": "Face Amount", "limit_amount": 500000.0, "currency": "USD"}
                ],
                "exclusions": ["Suicide within 2 years of policy date"],
            },
            source_page_hints={"policy_number": [1], "effective_date": [1]},
            extraction_notes="",
            tokens_in=1234,
            tokens_out=567,
            model_id="claude-opus-4-7",
            prompt_version="1.0",
        )

    def summarize(self, fields: dict, full_text: str) -> SummaryResult:
        self.summarize_calls += 1
        return SummaryResult(
            markdown=(
                f"## Policy Summary\n\n"
                f"**Policy** {fields.get('policy_number')} - Test\n"
            ),
            tokens_in=2000,
            tokens_out=300,
            model_id="claude-opus-4-7",
            prompt_version="1.0",
        )


@pytest.fixture
def mock_llm_provider() -> MockLLMProvider:
    """Fresh mock LLM provider per test."""
    return MockLLMProvider()


# -------------------- In-memory DB --------------------


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """Yield a SQLAlchemy session bound to an in-memory SQLite engine.

    Also monkeypatches `app.storage.engine` / `SessionLocal` / `settings.storage_dir`
    so that production code paths (like `save_pdf_bytes`) use the temp resources
    instead of the real ones.
    """
    # Ensure all SQLAlchemy models are imported and registered on Base.metadata
    # before we call create_all. (Models register themselves on import.)
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # share the single in-memory connection
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # Point the production module-level globals at the test engine so any code
    # path that imports them directly (e.g. `from app.storage import engine`)
    # sees the same in-memory DB.
    monkeypatch.setattr(storage_module, "engine", engine, raising=True)
    monkeypatch.setattr(storage_module, "SessionLocal", TestingSessionLocal, raising=True)

    # Redirect uploaded-PDF storage to a tmp dir so tests don't write to ./storage/
    tmp_storage = tmp_path / "pdfs"
    tmp_storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage_module.settings, "storage_dir", str(tmp_storage), raising=True)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        # Best-effort cleanup of tmp storage (tmp_path itself is auto-cleaned).
        shutil.rmtree(tmp_storage, ignore_errors=True)


# -------------------- FastAPI TestClient --------------------


@pytest.fixture
def client(tmp_db, mock_llm_provider):
    """FastAPI TestClient with `get_db` and `_get_llm` overridden.

    Uses the `tmp_db` fixture (in-memory SQLite session) and `mock_llm_provider`
    so requests never hit the real Anthropic API or the production database.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.storage import get_db

    # Lazy import so tests that just need a DB don't fail if routes are stale.
    try:
        from app.routes import _get_llm
    except ImportError:  # pragma: no cover - routes is required for the API tests
        _get_llm = None

    def _override_get_db():
        try:
            yield tmp_db
        finally:
            # The test fixture owns the session lifecycle; don't close it here.
            pass

    def _override_get_llm():
        return mock_llm_provider

    app.dependency_overrides[get_db] = _override_get_db
    if _get_llm is not None:
        app.dependency_overrides[_get_llm] = _override_get_llm

    with TestClient(app) as test_client:
        # Expose the mock for assertions in the tests.
        test_client.mock_llm = mock_llm_provider
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def sample_pdf_path() -> Path:
    """Path to the bundled Leland Stanford sample PDF."""
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Sample PDF not found at {SAMPLE_PDF}")
    return SAMPLE_PDF


@pytest.fixture
def sample_pdf_bytes(sample_pdf_path: Path) -> bytes:
    return sample_pdf_path.read_bytes()
