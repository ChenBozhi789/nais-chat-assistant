"""Helpers shared by the Foundry agent implementations (File Search and MCP).

Only neutral plumbing lives here: the agent client and display formatting. The
tool-specific logic stays in ``impl2_agent_file_search/`` and
``impl3_agent_mcp/``, which never import each other.

Auth uses Entra ID (``DefaultAzureCredential``): run ``az login`` locally.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from backend.answer_types import AnswerError

SNIPPET_CHARS = 200
_ACRONYMS = {"ai": "AI", "nais": "NAIS", "nz": "NZ"}


@lru_cache(maxsize=4)
def agent_client(agent_name_vars: tuple[str, ...]) -> Any:
    """OpenAI client bound to a Foundry agent (created once per setting, reused).

    ``agent_name_vars`` lists env vars to try in order, e.g.
    ``("FOUNDRY_MCP_AGENT_NAME", "FOUNDRY_AGENT_NAME")``.
    """
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    agent_name = next(
        (os.getenv(var, "") for var in agent_name_vars if os.getenv(var)), ""
    )
    missing = [
        label
        for label, value in {
            "FOUNDRY_PROJECT_ENDPOINT": endpoint,
            " or ".join(agent_name_vars): agent_name,
        }.items()
        if not value
    ]
    if missing:
        raise AnswerError(f"Missing .env settings: {', '.join(missing)}")
    project = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    return project.get_openai_client(agent_name=agent_name)


def display_title(filename: str, url: str) -> str:
    """Readable page name: ``about-us.md`` -> ``About us``.

    Prefers the URL slug (the real page), falls back to the file name, and drops
    the extension and trailing version tags such as ``-v02``.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    if not slug or slug.startswith(("http", "www")) or "." in slug:
        slug = filename.strip()
    slug = re.sub(r"\.md$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[-_ ]v\d+$", "", slug, flags=re.IGNORECASE)
    words = [w for w in re.split(r"[-_\s]+", slug) if w]
    if not words:
        return "NAIS"
    words = [_ACRONYMS.get(w.lower(), w.lower()) for w in words]
    words[0] = words[0] if words[0].isupper() else words[0].capitalize()
    return " ".join(words)


def snippet(text: str) -> str:
    """Body text for display: drop Markdown heading lines, collapse whitespace."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return " ".join(" ".join(lines or text.splitlines()).split())[:SNIPPET_CHARS]
