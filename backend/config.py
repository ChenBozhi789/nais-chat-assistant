"""Environment-based settings. All secrets and URLs come from env vars / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _csv(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


def _path(name: str, default: str) -> Path:
    """Resolve a path env var relative to the repo root."""
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else ROOT_DIR / p


@dataclass(frozen=True)
class Settings:
    # Microsoft Foundry model inference (key stays on the backend only)
    foundry_openai_base_url: str
    foundry_api_key: str
    foundry_chat_deployment: str
    foundry_embedding_deployment: str

    # Azure AI Search
    azure_search_endpoint: str
    azure_search_admin_key: str
    azure_search_query_key: str
    azure_search_index: str

    # RAG
    data_dir: Path
    chunk_tokens: int
    chunk_overlap_tokens: int
    top_k: int
    min_score: float
    nais_contact_info: str

    # API / security
    allowed_origins: list[str]
    rate_limit: str
    llm_timeout_seconds: float
    max_output_tokens: int

    def missing_model_settings(self) -> list[str]:
        """Names of required Foundry model settings that are not set."""
        required = {
            "FOUNDRY_OPENAI_BASE_URL": self.foundry_openai_base_url,
            "FOUNDRY_API_KEY": self.foundry_api_key,
            "FOUNDRY_CHAT_DEPLOYMENT": self.foundry_chat_deployment,
            "FOUNDRY_EMBEDDING_DEPLOYMENT": self.foundry_embedding_deployment,
        }
        return [k for k, v in required.items() if not v]

    def missing_embedding_settings(self) -> list[str]:
        """Required settings for a single embedding call."""
        required = {
            "FOUNDRY_OPENAI_BASE_URL": self.foundry_openai_base_url,
            "FOUNDRY_API_KEY": self.foundry_api_key,
            "FOUNDRY_EMBEDDING_DEPLOYMENT": self.foundry_embedding_deployment,
        }
        return [k for k, v in required.items() if not v]

    def missing_ingest_settings(self) -> list[str]:
        """Required settings for embedding and uploading reviewed chunks."""
        required = {
            "AZURE_SEARCH_ENDPOINT": self.azure_search_endpoint,
            "AZURE_SEARCH_ADMIN_KEY": self.azure_search_admin_key,
            "AZURE_SEARCH_INDEX": self.azure_search_index,
        }
        missing_search = [k for k, v in required.items() if not v]
        return [*self.missing_embedding_settings(), *missing_search]


def load_settings() -> Settings:
    return Settings(
        foundry_openai_base_url=os.getenv("FOUNDRY_OPENAI_BASE_URL", ""),
        foundry_api_key=os.getenv("FOUNDRY_API_KEY", ""),
        foundry_chat_deployment=os.getenv("FOUNDRY_CHAT_DEPLOYMENT", ""),
        foundry_embedding_deployment=os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT", ""),
        azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT", ""),
        azure_search_admin_key=os.getenv("AZURE_SEARCH_ADMIN_KEY", ""),
        azure_search_query_key=os.getenv("AZURE_SEARCH_QUERY_KEY", ""),
        azure_search_index=os.getenv("AZURE_SEARCH_INDEX", ""),
        data_dir=_path("DATA_DIR", "content/chunks/basic-pages"),
        chunk_tokens=int(os.getenv("CHUNK_TOKENS", "500")),
        chunk_overlap_tokens=int(os.getenv("CHUNK_OVERLAP_TOKENS", "50")),
        top_k=int(os.getenv("TOP_K", "4")),
        min_score=float(os.getenv("MIN_RELEVANCE_SCORE", "0.3")),
        nais_contact_info=os.getenv(
            "NAIS_CONTACT_INFO", "Please contact the NAIS team via the website."
        ),
        allowed_origins=_csv("ALLOWED_ORIGINS"),
        rate_limit=os.getenv("RATE_LIMIT", "5/minute"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "400")),
    )


settings = load_settings()


if __name__ == "__main__":
    # Quick check: `python -m backend.config` (never prints secret values).
    s = settings
    print(f"data_dir={s.data_dir}\nsearch_index={s.azure_search_index}")
    print(f"allowed_origins={s.allowed_origins}")
    missing = s.missing_model_settings()
    print("Foundry settings:", "OK" if not missing else f"missing {missing}")
