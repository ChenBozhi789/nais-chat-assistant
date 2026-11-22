"""Foundry client for the offline ``rag`` scripts (embedding questions and chunks)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from backend.config import settings


def new_foundry_client() -> Any:
    """Use a Foundry deployment's OpenAI-compatible inference endpoint."""
    from openai import OpenAI

    missing = settings.missing_embedding_settings()
    if missing:
        raise ValueError(f"Missing .env settings: {', '.join(missing)}")
    parsed = urlparse(settings.foundry_openai_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path.rstrip("/") != "/openai/v1"
    ):
        raise ValueError(
            "FOUNDRY_OPENAI_BASE_URL must be an HTTPS inference URL ending "
            "in /openai/v1/ (not a Foundry project URL)"
        )
    return OpenAI(
        base_url=settings.foundry_openai_base_url,
        api_key=settings.foundry_api_key,
        timeout=60.0,
    )
