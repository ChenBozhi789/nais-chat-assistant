"""Scoring rules and the run loop of evaluate_answers (no network)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_answers as ev  # noqa: E402

FACT = {
    "id": "loc",
    "type": "fact",
    "q": "Where?",
    "expected_urls": ["https://aisandbox.org.nz/about-us/"],
    "expected_facts": ["Halifax"],
    "expected_any": ["28 Halifax"],
}


def test_fact_passes_with_facts_and_url() -> None:
    sources = [{"title": "T", "url": "https://aisandbox.org.nz/about-us/"}]
    out = ev.check(FACT, "At 28 Halifax Street.", sources)
    assert out["answer_pass"] and out["checks"]["source_hit"]


def test_fact_fails_when_fact_missing() -> None:
    assert not ev.check(FACT, "Somewhere in Nelson.", [])["answer_pass"]


def test_wrong_source_url_is_reported_but_does_not_fail_the_answer() -> None:
    out = ev.check(FACT, "28 Halifax", [{"title": "x", "url": "https://other/"}])
    assert out["answer_pass"] and out["checks"]["source_hit"] is False


def test_sources_without_urls_are_not_judged() -> None:
    out = ev.check(FACT, "28 Halifax", [{"title": "about-us.md"}])  # File Search style
    assert "source_hit" not in out["checks"] and out["answer_pass"]


def test_unknown_requires_fallback_and_no_forbidden_text() -> None:
    item = {"id": "u", "type": "unknown", "q": "?", "must_not": ["$"]}
    assert ev.check(item, "Sorry, I don't know.", [])["answer_pass"]
    assert not ev.check(item, "It costs $20.", [])["answer_pass"]
    assert not ev.check(item, "It is free.", [])["answer_pass"]  # no fallback


def test_dash_variants_match() -> None:
    assert ev.contains("sessions run 20–30 minutes", "20-30")


def test_evaluate_scores_each_question_and_survives_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(question: str, history: list[dict[str, str]]) -> NS:
        if question == "boom":
            raise RuntimeError("down")
        return NS(text="28 Halifax", sources=[])

    monkeypatch.setattr(ev, "load_answer_function", lambda impl: fake)
    questions = [FACT, {**FACT, "id": "bad", "q": "boom"}]
    results = ev.evaluate("mcp", questions)
    assert [r["answer_pass"] for r in results] == [True, False]
    assert results[1]["answer"].startswith("ERROR")


def test_select_questions_filters() -> None:
    args = argparse.Namespace(only="loc,hours", type="fact", limit=1)
    picked = ev.select_questions(args)
    assert len(picked) == 1 and picked[0]["id"] in {"loc", "hours"}


def test_every_impl_module_exposes_answer_question() -> None:
    for impl in ev.IMPLEMENTATIONS:
        assert callable(ev.load_answer_function(impl))
