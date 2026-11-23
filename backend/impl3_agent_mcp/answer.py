"""Implementation 3: Foundry agent + remote MCP tool calling.

The agent (configured in the Foundry portal) is given the NAIS documents MCP
server (``mcp_tools/mcp-server``). When it wants to call a tool the Responses API
sends an ``mcp_approval_request``; this module auto-approves only the read-only
tools on the configured server and builds sources from the tool results.

Independent of the Chat Completions and File Search implementations. Exposes
``answer_question`` returning ``backend.answer_types.Answer``.
Agent name: ``FOUNDRY_MCP_AGENT_NAME`` (falls back to ``FOUNDRY_AGENT_NAME``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from backend.agent_common import agent_client, display_title, snippet
from backend.answer_types import Answer, AnswerError
from backend.config import settings

logger = logging.getLogger("nais.impl3_agent_mcp")

AGENT_NAME_VARS = ("FOUNDRY_MCP_AGENT_NAME", "FOUNDRY_AGENT_NAME")
MAX_SOURCES = 3
MIN_ANSWER_OVERLAP = 0.3  # share of answer words that must appear in a source
MAX_APPROVAL_ROUNDS = 6  # list_documents + a few read_document calls, then answer
TOTAL_DEADLINE_SECONDS = 50.0  # stop starting new rounds; widget gives up at 75 s
DEFAULT_MCP_TOOLS = "list_documents,read_document"
# Allowed argument names per approved MCP tool (anything else is denied).
_MCP_TOOL_ARGS = {"list_documents": set(), "read_document": {"filename"}}

__all__ = ["Answer", "AnswerError", "answer_question"]


def _approved_mcp_tools() -> set[str]:
    raw = os.getenv("FOUNDRY_MCP_APPROVED_TOOLS", DEFAULT_MCP_TOOLS)
    return {t.strip() for t in raw.split(",") if t.strip()} & set(_MCP_TOOL_ARGS)


def _approve_mcp_request(request: Any) -> bool:
    """Approve only the configured NAIS MCP server's read-only tools.

    Fail closed: no configured server label, a different server, an unlisted
    tool, or unexpected arguments are all denied.
    """
    label = os.getenv("FOUNDRY_MCP_SERVER_LABEL", "").strip()
    name = getattr(request, "name", "") or ""
    if not label or getattr(request, "server_label", None) != label:
        return False
    if name not in _approved_mcp_tools():
        return False
    try:
        arguments = json.loads(getattr(request, "arguments", "") or "{}")
    except ValueError:
        return False
    if not isinstance(arguments, dict) or not set(arguments) <= _MCP_TOOL_ARGS[name]:
        return False
    return name != "read_document" or isinstance(arguments.get("filename"), str)


def _resolve_approvals(
    client: Any, conversation_id: str, response: Any
) -> tuple[Any, list[Any]]:
    """Answer ``mcp_approval_request`` items until the agent produces its reply.

    Returns the final response and the output items of every round, since the
    tool results used for sources arrive in earlier rounds.
    """
    items: list[Any] = []
    deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
    for _ in range(MAX_APPROVAL_ROUNDS):
        if time.monotonic() > deadline:
            raise AnswerError("Agent exceeded the total time limit")
        round_items = list(getattr(response, "output", None) or [])
        items.extend(round_items)
        requests = [
            i for i in round_items if getattr(i, "type", None) == "mcp_approval_request"
        ]
        if not requests:
            return response, items
        decisions = []
        for req in requests:
            approved = _approve_mcp_request(req)
            logger.info(
                "mcp approval: tool=%s approved=%s", getattr(req, "name", "?"), approved
            )
            decisions.append(
                {
                    "type": "mcp_approval_response",
                    "approval_request_id": req.id,
                    "approve": approved,
                }
            )
        response = client.responses.create(
            conversation=conversation_id,
            input=decisions,
            timeout=settings.llm_timeout_seconds,
        )
    raise AnswerError("Agent needed too many tool approvals")


def _source_from_read_document(output: str) -> dict[str, str] | None:
    """Source from the NAIS MCP ``read_document`` result (filename, etag, text)."""
    try:
        doc = json.loads(output)
    except ValueError:
        return None
    if (
        not isinstance(doc, dict)
        or "error" in doc
        or not isinstance(doc.get("text"), str)
    ):
        return None
    filename = str(doc.get("filename", ""))
    text = doc["text"]
    url_match = re.match(r"\s*(https?://\S+)", text)  # files begin with the page URL
    url = url_match.group(1) if url_match else ""
    body = text[url_match.end() :] if url_match else text
    source = {
        "title": display_title(filename, url),
        "snippet": snippet(body),
        "snippet_full": body,  # internal; stripped before returning
    }
    if url:
        source["url"] = url
    return source


def _sources_from_mcp_call(output: str) -> list[dict[str, str]]:
    """Parse knowledge-base results returned by the agent's MCP retrieval tool.

    Each document's content looks like ``Title: x.md`` then ``Content Snippet:``
    followed by the page URL and body text.
    """
    try:
        documents = json.loads(output).get("documents", [])
    except (ValueError, AttributeError):
        return []
    found: list[dict[str, str]] = []
    for doc in documents:
        content = doc.get("content", "") if isinstance(doc, dict) else ""
        title_match = re.search(r"^Title:\s*(.+)$", content, re.MULTILINE)
        body = content.split("Content Snippet:", 1)[-1].strip()
        url_match = re.match(r"https?://\S+", body)
        url = url_match.group(0) if url_match else ""
        text = body[len(url) :] if url else body
        source = {
            "title": display_title(title_match.group(1) if title_match else "", url),
            "snippet": snippet(text),
            "snippet_full": text,  # internal; stripped before returning
        }
        if url:
            source["url"] = url
        found.append(source)
    return found


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def _keep_used(sources: list[dict[str, str]], answer: str) -> list[dict[str, str]]:
    """Keep only sources the answer appears to draw on.

    The retrieval tool returns ~20 unscored chunks, most irrelevant. Rank them by
    the share of the answer's words found in each chunk's text and keep the best.
    """
    answer_words = _words(answer)
    if not answer_words:
        return []
    scored = [
        (len(answer_words & _words(s["snippet_full"])) / len(answer_words), s)
        for s in sources
    ]
    scored = [(r, s) for r, s in scored if r >= MIN_ANSWER_OVERLAP]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [s for _, s in scored[:MAX_SOURCES]]


def _extract_sources(items: list[Any], answer: str) -> list[dict[str, str]]:
    """One source per page, taken from the MCP tool results of every round."""
    sources: dict[str, dict[str, str]] = {}
    for item in items:
        if getattr(item, "type", None) != "mcp_call" or not item.output:
            continue
        if getattr(item, "name", None) == "read_document":
            doc_src = _source_from_read_document(item.output)
            if doc_src:
                sources.setdefault(doc_src.get("url") or doc_src["title"], doc_src)
            continue
        for src in _sources_from_mcp_call(item.output):
            sources.setdefault(src.get("url") or src["title"], src)
    used = _keep_used(list(sources.values()), answer)
    for src in used:
        src.pop("snippet_full", None)
    return used


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
        response, items = _resolve_approvals(client, conversation.id, response)
    except AnswerError:
        raise
    except Exception as exc:  # azure/openai raise many types; callers need one
        raise AnswerError(f"Agent request failed: {type(exc).__name__}") from exc

    text = (response.output_text or "").strip()
    sources = _extract_sources(items, text)
    logger.info("mcp agent chat: question_chars=%d sources=%d", len(question), len(sources))
    if not text:
        raise AnswerError("Agent returned an empty answer")
    return Answer(text=text, sources=sources)
