import uuid
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_storage_dir() -> None:
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)


def save_pdf_bytes(content: bytes, original_filename: str) -> tuple[str, str]:
    ensure_storage_dir()
    document_id = uuid.uuid4().hex
    path = Path(settings.storage_dir) / f"{document_id}.pdf"
    path.write_bytes(content)
    return document_id, str(path.resolve())


def get_pdf_path(document_id: str) -> Path:
    return Path(settings.storage_dir) / f"{document_id}.pdf"
