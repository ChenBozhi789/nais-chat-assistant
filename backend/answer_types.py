"""Types shared by every answer implementation.

Each ``answer_*.py`` module exposes ``answer_question(question, history)`` that
returns an ``Answer`` and raises ``AnswerError`` on any model/tool failure.
"""

from __future__ import annotations

from dataclasses import dataclass


class AnswerError(Exception):
    """A model, tool or vector store failure that should become a friendly API error."""


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[dict[str, str]]
