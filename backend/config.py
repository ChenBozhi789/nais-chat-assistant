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

    # Local vector store
    chroma_path: Path
    chroma_collection: str

    # Legacy Azure AI Search export settings
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
    allow_localhost: bool
    rate_limit: str
    llm_timeout_seconds: float
    max_output_tokens: int

    # Text-to-speech (Azure Speech / AI Services)
    azure_speech_key: str
    azure_speech_region: str
    azure_speech_endpoint: str
    azure_speech_voice: str
    tts_rate_limit: str

    def speech_ready(self) -> bool:
        """TTS needs a key plus either a region or a full endpoint URL."""
        return bool(self.azure_speech_key and (self.azure_speech_region or self.azure_speech_endpoint))

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

def load_settings() -> Settings:
    return Settings(
        foundry_openai_base_url=os.getenv("FOUNDRY_OPENAI_BASE_URL", ""),
        foundry_api_key=os.getenv("FOUNDRY_API_KEY", ""),
        foundry_chat_deployment=os.getenv("FOUNDRY_CHAT_DEPLOYMENT", ""),
        foundry_embedding_deployment=os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT", ""),
        chroma_path=_path("CHROMA_PATH", "data/chroma"),
        chroma_collection=os.getenv("CHROMA_COLLECTION", "nais-content"),
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
        allow_localhost=os.getenv("ALLOW_LOCALHOST", "true").lower() == "true",
        rate_limit=os.getenv("RATE_LIMIT", "5/minute"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "400")),
        # Falls back to the Foundry key, which works for multi-service AI Services resources.
        azure_speech_key=os.getenv("AZURE_SPEECH_KEY") or os.getenv("FOUNDRY_API_KEY", ""),
        azure_speech_region=os.getenv("AZURE_SPEECH_REGION", ""),
        azure_speech_endpoint=os.getenv("AZURE_SPEECH_ENDPOINT", ""),
        azure_speech_voice=os.getenv("AZURE_SPEECH_VOICE", "en-NZ-MollyNeural"),
        tts_rate_limit=os.getenv("TTS_RATE_LIMIT", "10/minute"),
    )


settings = load_settings()


if __name__ == "__main__":
    # Quick check: `python -m backend.config` (never prints secret values).
    s = settings
    print(f"data_dir={s.data_dir}\nchroma_path={s.chroma_path}")
    print(f"chroma_collection={s.chroma_collection}")
    print(f"allowed_origins={s.allowed_origins}")
    missing = s.missing_model_settings()
    print("Foundry settings:", "OK" if not missing else f"missing {missing}")
