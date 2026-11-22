"""Implementation 2: Foundry agent + File Search (configured in the Foundry portal).

The agent owns retrieval through its File Search tool, so there is no local
embedding, vector store or MCP handling here. Sources come from the
``file_citation`` / ``url_citation`` annotations on the agent's reply.

Independent of the Chat Completions and MCP implementations. Exposes
``answer_question`` returning ``backend.answer_types.Answer``.
Agent name: ``FOUNDRY_FILE_SEARCH_AGENT_NAME`` (falls back to ``FOUNDRY_AGENT_NAME``).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.agent_common import agent_client
from backend.answer_types import Answer, AnswerError
from backend.config import settings

logger = logging.getLogger("nais.impl2_agent_file_search")

AGENT_NAME_VARS = ("FOUNDRY_FILE_SEARCH_AGENT_NAME", "FOUNDRY_AGENT_NAME")

__all__ = ["Answer", "AnswerError", "answer_question"]


def _extract_sources(response: Any) -> list[dict[str, str]]:
    """One source per cited file or URL, from the reply's message annotations."""
    sources: dict[str, dict[str, str]] = {}
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                kind = getattr(ann, "type", None)
                if kind == "url_citation":
                    url = getattr(ann, "url", "") or ""
                    title = getattr(ann, "title", "") or url
                    key = url or title
                elif kind == "file_citation":
                    url = ""
                    title = getattr(ann, "filename", "") or "NAIS document"
                    key = getattr(ann, "file_id", "") or title
                else:
                    continue
                if key not in sources:
                    sources[key] = {"title": title, "snippet": ""}
                    if url:
                        sources[key]["url"] = url
    return list(sources.values())


def answer_question(question: str, history: list[dict[str, str]]) -> Answer:
    """Ask the agent. Each request gets a fresh conversation seeded with history."""
    try:
        client = agent_client(AGENT_NAME_VARS)
        conversation = client.conversations.create(
            items=[{"role": h["role"], "content": h["content"]} for h in history]
        )
        response = client.responses.create(
            conversation=conversation.id,
            input=[{"role": "user", "content": question}],
            timeout=settings.llm_timeout_seconds,
        )
    except AnswerError:
        raise
    except Exception as exc:  # azure/openai raise many types; callers need one
        raise AnswerError(f"Agent request failed: {type(exc).__name__}") from exc

    text = (response.output_text or "").strip()
    if not text:
        raise AnswerError("Agent returned an empty answer")
    sources = _extract_sources(response)
    logger.info("file search chat: question_chars=%d sources=%d", len(question), len(sources))
    return Answer(text=text, sources=sources)
