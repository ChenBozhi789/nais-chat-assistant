"""File Search agent: source extraction and failure handling (no network)."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import pytest
from backend.impl2_agent_file_search import answer as fs


def _reply(*annotations: Any) -> Any:
    part = NS(annotations=list(annotations))
    return NS(output=[NS(type="message", content=[part])], output_text="An answer.")


def test_sources_from_file_and_url_citations() -> None:
    reply = _reply(
        NS(type="file_citation", filename="about-us.md", file_id="f1"),
        NS(type="file_citation", filename="about-us.md", file_id="f1"),  # duplicate
        NS(type="url_citation", url="https://aisandbox.org.nz/x/", title="X page"),
        NS(type="other"),
    )
    assert fs._extract_sources(reply) == [
        {"title": "about-us.md", "snippet": ""},
        {"title": "X page", "snippet": "", "url": "https://aisandbox.org.nz/x/"},
    ]


def test_no_annotations_means_no_sources() -> None:
    assert fs._extract_sources(NS(output=[NS(type="message", content=[])])) == []


def test_answer_question_wraps_client_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: tuple[str, ...]) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(fs, "agent_client", boom)
    with pytest.raises(fs.AnswerError):
        fs.answer_question("hi", [])


def test_answer_question_returns_text_and_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = _reply(NS(type="file_citation", filename="a.md", file_id="f"))
    client = NS(
        conversations=NS(create=lambda **_: NS(id="c1")),
        responses=NS(create=lambda **_: reply),
    )
    monkeypatch.setattr(fs, "agent_client", lambda _: client)
    result = fs.answer_question("hi", [{"role": "user", "content": "yo"}])
    assert result.text == "An answer." and result.sources[0]["title"] == "a.md"
