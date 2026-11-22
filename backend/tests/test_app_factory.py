"""Shared API: /chat and /health work with any injected answer function."""

from __future__ import annotations

from backend.answer_types import Answer, AnswerError
from backend.app_factory import create_app
from fastapi.testclient import TestClient


def _client(answer_fn) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(create_app(answer_fn))


def test_health() -> None:
    assert _client(lambda q, h: Answer("x", [])).get("/health").json() == {"status": "ok"}


def test_chat_returns_injected_answer_and_trims_history() -> None:
    seen: dict[str, object] = {}

    def fake(question: str, history: list[dict[str, str]]) -> Answer:
        seen["question"], seen["turns"] = question, len(history)
        return Answer("hello", [{"title": "T", "snippet": "s"}])

    history = [{"role": "user", "content": str(i)} for i in range(10)]
    res = _client(fake).post("/chat", json={"message": " hi ", "history": history})
    assert res.status_code == 200
    assert res.json()["answer"] == "hello"
    assert seen == {"question": "hi", "turns": 6}


def test_chat_maps_answer_error_to_503() -> None:
    def failing(question: str, history: list[dict[str, str]]) -> Answer:
        raise AnswerError("boom")

    res = _client(failing).post("/chat", json={"message": "hi"})
    assert res.status_code == 503
    assert "boom" not in res.text


def test_chat_rejects_blank_message() -> None:
    assert _client(lambda q, h: Answer("x", [])).post("/chat", json={"message": "  "}).status_code == 422
