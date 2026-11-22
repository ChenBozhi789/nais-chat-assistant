"""Chat Completions + Chroma: fallback, sources and prompt wiring (no network)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from backend.impl1_chat_completions_chroma import answer as impl


def _chunk(similarity: float, url: str = "https://aisandbox.org.nz/a/", cid: str = "a-1"):
    return impl.Chunk(
        id=cid, title="A", url=url, content="# Heading\nBody text.", similarity=similarity
    )


def test_below_threshold_returns_fallback_without_calling_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(impl, "retrieve", lambda q: [_chunk(0.0)])
    monkeypatch.setattr(impl, "settings", replace(impl.settings, min_score=0.5))

    def no_model(*_: object) -> str:
        raise AssertionError("model must not be called")

    monkeypatch.setattr(impl, "_generate", no_model)
    result = impl.answer_question("unrelated", [])
    assert result.sources == [] and "don't know" in result.text


def test_no_chunks_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(impl, "retrieve", lambda q: [])
    assert impl.answer_question("q", []).sources == []


def test_relevant_chunks_are_passed_to_model_and_cited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(impl, "retrieve", lambda q: [_chunk(0.9)])
    monkeypatch.setattr(impl, "settings", replace(impl.settings, min_score=0.5))
    monkeypatch.setattr(impl, "_generate", lambda q, h, c: "grounded answer")
    result = impl.answer_question("q", [])
    assert result.text == "grounded answer"
    assert result.sources == [
        {"title": "A", "snippet": "Body text.", "url": "https://aisandbox.org.nz/a/"}
    ]


def test_sources_are_one_per_url() -> None:
    chunks = [_chunk(0.9, cid="a-1"), _chunk(0.8, cid="a-2"), _chunk(0.7, url="", cid="b")]
    assert [s.get("url") for s in impl._build_sources(chunks)] == [
        "https://aisandbox.org.nz/a/",
        None,
    ]


def test_prompt_contains_context_history_and_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def create(**kwargs: object) -> object:
        captured.update(kwargs)
        return NS(choices=[NS(message=NS(content=" ok "))])

    monkeypatch.setattr(
        impl, "_client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    history = [{"role": "user", "content": "earlier"}]
    assert impl._generate("now?", history, [_chunk(0.9)]) == "ok"
    messages = captured["messages"]
    assert "Body text." in messages[0]["content"]  # type: ignore[index]
    assert messages[1] == history[0] and messages[-1]["content"] == "now?"  # type: ignore[index]
