from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.storage import Base, engine, ensure_storage_dir


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lazy import models inside lifespan so SQLAlchemy registers all tables
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_storage_dir()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Insurance Document Summarizer", lifespan=lifespan)

    @app.get("/healthz")
    def healthz():
        return {
            "status": "ok",
            "environment": settings.environment,
            "model": settings.anthropic_model,
        }

    try:
        from app.routes import router

        app.include_router(router)
    except ImportError:
        pass

    return app


app = create_app()
