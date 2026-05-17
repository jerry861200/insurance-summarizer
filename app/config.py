from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_model: str = "claude-opus-4-7"
    openai_model: str = "gpt-4o"
    openai_api_key: str = ""
    database_url: str = "sqlite:///./insurance.db"
    storage_dir: str = "./storage/pdfs"
    max_pdf_mb: int = 20
    environment: Literal["dev", "prod"] = "dev"
    # Opt-in cross-model verification: when True, AND both keys are set, the
    # pipeline runs a second LLM after the primary and surfaces scalar-field
    # disagreements as ValidationWarnings. See app/extractors/llm/verifier.py.
    cross_model_verify: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
