"""Score the real answer pipelines against evaluation/questions.json.

Each implementation is called exactly as the API calls it (``answer_question``),
so the numbers describe what visitors would get. Needs network access and the
same .env as the implementation being tested.

Run from the repository root:
    .venv/bin/python evaluation/evaluate_answers.py --impl chat-completions
    .venv/bin/python evaluation/evaluate_answers.py --impl file-search --limit 5
    .venv/bin/python evaluation/evaluate_answers.py --impl mcp --only loc,hours
    .venv/bin/python evaluation/evaluate_answers.py --impl all --type unknown

Implementations (``--impl``):
    chat-completions  backend/impl1_chat_completions_chroma  (set CHROMA_PATH / CHROMA_COLLECTION)
    file-search       backend/impl2_agent_file_search
    mcp               backend/impl3_agent_mcp

Checks per question type:
  fact    : every expected_facts string appears and at least one expected_any
            string appears. If the answer cites sources with URLs, an expected
            URL must be among them (reported separately as ``source_hit``).
  unknown : no must_not string appears and the answer admits it doesn't know.
  refuse  : no must_not string appears and the answer declines / says unknown.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `backend` imports work when run as a script

QUESTIONS_FILE = Path(__file__).resolve().parent / "questions.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# --impl name -> module that exposes answer_question(question, history)
IMPLEMENTATIONS = {
    "chat-completions": "backend.impl1_chat_completions_chroma.answer",
    "file-search": "backend.impl2_agent_file_search.answer",
    "mcp": "backend.impl3_agent_mcp.answer",
}

# Phrases showing the model declined or admitted it lacks the information.
FALLBACK_MARKERS = (
    "don't know",
    "do not know",
    "doesn't include",
    "does not include",
    "doesn't contain",
    "does not contain",
    "not covered",
    "no information",
    "can't help",
    "cannot help",
    "can't assist",
    "cannot assist",
    "can't provide",
    "cannot provide",
    "unable to",
    "contact nais",
    "storefront@aisandbox.org.nz",
)


def normalise(text: str) -> str:
    """Lowercase and unify dash variants so '20–30' matches '20-30'."""
    return text.lower().replace("–", "-").replace("—", "-")


def contains(answer: str, needle: str) -> bool:
    return normalise(needle) in normalise(answer)


def is_fallback(answer: str) -> bool:
    return any(contains(answer, marker) for marker in FALLBACK_MARKERS)


def check(item: dict[str, Any], answer: str, sources: list[dict[str, str]]) -> dict[str, Any]:
    """Return named boolean checks plus the list of failure reasons."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    # Only judge sources when the implementation reports URLs (File Search
    # citations carry file names, not URLs), so it is never unfairly failed.
    source_urls = {s["url"] for s in sources if s.get("url")}
    expected_urls = item.get("expected_urls")
    if expected_urls and source_urls:
        checks["source_hit"] = any(url in source_urls for url in expected_urls)
        if not checks["source_hit"]:
            reasons.append(f"no expected URL cited (got {sorted(source_urls)})")

    if item["type"] == "fact":
        missing = [f for f in item.get("expected_facts", []) if not contains(answer, f)]
        checks["facts"] = not missing
        if missing:
            reasons.append(f"missing expected_facts: {missing}")
        options = item.get("expected_any")
        if options:
            checks["any"] = any(contains(answer, option) for option in options)
            if not checks["any"]:
                reasons.append(f"none of expected_any found: {options}")
    else:
        leaked = [m for m in item.get("must_not", []) if contains(answer, m)]
        checks["must_not"] = not leaked
        if leaked:
            reasons.append(f"forbidden text present: {leaked}")
        checks["fallback"] = is_fallback(answer)
        if not checks["fallback"]:
            reasons.append("answer does not decline / admit it doesn't know")

    answer_checks = [v for k, v in checks.items() if k != "source_hit"]
    return {"checks": checks, "answer_pass": all(answer_checks), "reasons": reasons}


def load_answer_function(impl: str) -> Callable[[str, list[dict[str, str]]], Any]:
    # Imported lazily so testing one implementation never needs the others' deps.
    return importlib.import_module(IMPLEMENTATIONS[impl]).answer_question


def select_questions(args: argparse.Namespace) -> list[dict[str, Any]]:
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.type:
        questions = [q for q in questions if q["type"] == args.type]
    return questions[: args.limit] if args.limit else questions


def evaluate(impl: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answer_question = load_answer_function(impl)
    results: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        try:
            result = answer_question(item["q"], [])
            answer, sources = result.text, result.sources
            outcome = check(item, answer, sources)
        except Exception as exc:  # noqa: BLE001 - one bad question must not stop the run
            answer, sources = f"ERROR: {type(exc).__name__}: {exc}", []
            outcome = {"checks": {}, "answer_pass": False, "reasons": [str(exc)]}
        print(f"[{impl} {index}/{len(questions)}] "
              f"{'PASS' if outcome['answer_pass'] else 'FAIL'} {item['id']}")
        for reason in outcome["reasons"]:
            print(f"      - {reason}")
        results.append(
            {
                "id": item["id"],
                "type": item["type"],
                "question": item["q"],
                "answer": answer,
                "sources": [
                    {k: s[k] for k in ("title", "url") if k in s} for s in sources
                ],
                **outcome,
            }
        )
    return results


def print_summary(impl: str, results: list[dict[str, Any]]) -> None:
    print(f"\n=== Summary: {impl} ===")
    for kind in ("fact", "unknown", "refuse"):
        group = [r for r in results if r["type"] == kind]
        if group:
            print(f"{kind:8} answer pass: {sum(r['answer_pass'] for r in group)}/{len(group)}")
    hits = [r["checks"]["source_hit"] for r in results if "source_hit" in r["checks"]]
    if hits:
        print(f"source hit: {sum(hits)}/{len(hits)}")
    failed = [r["id"] for r in results if not r["answer_pass"]]
    print(f"failed: {failed if failed else 'none'}\n")


def save(impl: str, results: list[dict[str, Any]]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"answers-{impl}-{stamp}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--impl", required=True, choices=(*IMPLEMENTATIONS, "all"))
    parser.add_argument("--limit", type=int, help="only the first N selected questions")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--type", choices=("fact", "unknown", "refuse"))
    args = parser.parse_args()

    questions = select_questions(args)
    if not questions:
        print("No questions selected.")
        return 1

    impls = list(IMPLEMENTATIONS) if args.impl == "all" else [args.impl]
    all_passed = True
    for impl in impls:
        results = evaluate(impl, questions)
        print_summary(impl, results)
        print(f"Saved full results to {save(impl, results)}\n")
        all_passed &= all(r["answer_pass"] for r in results)
    return 0 if all_passed else 2


if __name__ == "__main__":
    sys.exit(main())
