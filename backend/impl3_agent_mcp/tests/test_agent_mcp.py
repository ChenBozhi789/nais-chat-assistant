"""MCP approval policy and source extraction in backend.impl3_agent_mcp (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import pytest
from backend.impl3_agent_mcp import answer as agent


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_MCP_SERVER_LABEL", "nais_documents")
    monkeypatch.delenv("FOUNDRY_MCP_APPROVED_TOOLS", raising=False)


def _req(
    name: str, args: object, label: str = "nais_documents", rid: str = "r1"
) -> Any:
    return NS(
        type="mcp_approval_request",
        id=rid,
        name=name,
        server_label=label,
        arguments=args if isinstance(args, str) else json.dumps(args),
    )


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (_req("list_documents", {}), True),
        (_req("read_document", {"filename": "about-us.md"}), True),
        (_req("delete_document", {"filename": "x.md"}), False),  # unlisted tool
        (
            _req("read_document", {"filename": "a.md"}, label="other"),
            False,
        ),  # other server
        (_req("read_document", {"filename": "a.md", "extra": 1}), False),  # extra args
        (_req("read_document", {"filename": 5}), False),
        (_req("read_document", "not json"), False),
    ],
)
def test_approval_policy(request_: Any, expected: bool) -> None:
    assert agent._approve_mcp_request(request_) is expected


def test_denies_everything_when_label_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_MCP_SERVER_LABEL")
    assert agent._approve_mcp_request(_req("list_documents", {})) is False


def test_approval_loop_sends_decisions_and_collects_items() -> None:
    first = NS(
        output=[_req("read_document", {"filename": "a.md"}), _req("rm", {}, rid="r2")]
    )
    final = NS(output=[NS(type="message", content=[])], output_text="ok")
    sent: list[Any] = []

    class Responses:
        def create(self, **kwargs: Any) -> Any:
            sent.append(kwargs["input"])
            return final

    client = NS(responses=Responses())
    response, items = agent._resolve_approvals(client, "conv", first)
    assert response is final and len(items) == 3
    assert sent[0] == [
        {"type": "mcp_approval_response", "approval_request_id": "r1", "approve": True},
        {
            "type": "mcp_approval_response",
            "approval_request_id": "r2",
            "approve": False,
        },
    ]


def test_approval_loop_is_bounded() -> None:
    looping = NS(output=[_req("list_documents", {})])
    client = NS(responses=NS(create=lambda **_: looping))
    with pytest.raises(agent.AnswerError):
        agent._resolve_approvals(client, "conv", looping)


def test_sources_from_read_document_result() -> None:
    text = (
        "https://aisandbox.org.nz/book-a-session/\n\n# Book a Free Session\n\n"
        "Booking is required to secure a time."
    )
    call = NS(
        type="mcp_call",
        name="read_document",
        output=json.dumps(
            {"filename": "book-a-session.md", "etag": '"e"', "text": text}
        ),
    )
    sources = agent._extract_sources([call], "Booking is required to secure a time.")
    assert sources == [
        {
            "title": "Book a session",
            "snippet": "Booking is required to secure a time.",
            "url": "https://aisandbox.org.nz/book-a-session/",
        }
    ]


def test_error_results_produce_no_source() -> None:
    call = NS(
        type="mcp_call", name="read_document", output=json.dumps({"error": "not_found"})
    )
    assert agent._extract_sources([call], "anything about booking sessions") == []
