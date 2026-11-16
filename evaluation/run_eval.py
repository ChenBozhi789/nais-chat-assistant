"""Evaluate retrieval and answer grounding against evaluation/questions.json.

Run from the repository root:
    .venv/bin/python evaluation/run_eval.py --limit 8
    .venv/bin/python evaluation/run_eval.py --only loc,hours --type fact

Checks per question type:
  fact    : an expected URL was retrieved, every expected_facts string appears,
            and at least one expected_any string appears.
  unknown : no must_not string appears and the answer admits it doesn't know.
  refuse  : no must_not string appears and the answer declines / says unknown.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "chroma"))

import practice  # noqa: E402  (reuses the Chroma + Foundry helpers)

QUESTIONS_FILE = HERE / "questions.json"
RESULTS_DIR = HERE / "results"

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


def retrieve(client: Any, collection: Any, question: str, limit: int) -> list[dict]:
    result = collection.query(
        query_embeddings=[practice.embed_question(client, question)],
        n_results=limit,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "id": record_id,
            "title": (meta or {}).get("title", "(untitled)"),
            "url": (meta or {}).get("url", ""),
            "content": doc or "",
            "distance": distance,
        }
        for record_id, doc, meta, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        )
    ]


def check(item: dict, answer: str, chunks: list[dict]) -> dict[str, Any]:
    """Return named boolean checks plus the list of failure reasons."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    expected_urls = item.get("expected_urls")
    if expected_urls:
        urls = {chunk["url"] for chunk in chunks}
        checks["retrieval_hit"] = any(url in urls for url in expected_urls)
        if not checks["retrieval_hit"]:
            reasons.append(f"no expected URL retrieved (got {sorted(urls)})")

    kind = item["type"]
    if kind == "fact":
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

    answer_checks = [v for k, v in checks.items() if k != "retrieval_hit"]
    return {
        "checks": checks,
        "answer_pass": all(answer_checks),
        "reasons": reasons,
    }


def run(args: argparse.Namespace) -> int:
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.type:
        questions = [q for q in questions if q["type"] == args.type]
    if not questions:
        print("No questions selected.")
        return 1

    collection = practice.open_collection()
    if collection.count() == 0:
        raise ValueError("Chroma collection is empty; run practice.py ingest first")
    client = practice.make_client(
        "FOUNDRY_EMBEDDING_DEPLOYMENT", "FOUNDRY_CHAT_DEPLOYMENT"
    )

    results: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        try:
            chunks = retrieve(client, collection, item["q"], args.limit)
            answer = practice.generate_answer(client, item["q"], chunks)
            outcome = check(item, answer, chunks)
        except (RuntimeError, ValueError) as exc:
            answer = f"ERROR: {exc}"
            chunks = []
            outcome = {"checks": {}, "answer_pass": False, "reasons": [str(exc)]}
        passed = outcome["answer_pass"]
        print(f"[{index}/{len(questions)}] {'PASS' if passed else 'FAIL'} {item['id']}")
        for reason in outcome["reasons"]:
            print(f"      - {reason}")
        results.append(
            {
                "id": item["id"],
                "type": item["type"],
                "question": item["q"],
                "answer": answer,
                "retrieved": [
                    {k: c[k] for k in ("id", "url", "distance")} for c in chunks
                ],
                **outcome,
            }
        )

    print_summary(results)
    save(results, args)
    return 0 if all(r["answer_pass"] for r in results) else 2


def print_summary(results: list[dict[str, Any]]) -> None:
    print("\n=== Summary ===")
    for kind in ("fact", "unknown", "refuse"):
        group = [r for r in results if r["type"] == kind]
        if group:
            passed = sum(r["answer_pass"] for r in group)
            print(f"{kind:8} answer pass: {passed}/{len(group)}")
    hits = [r["checks"]["retrieval_hit"] for r in results if "retrieval_hit" in r["checks"]]
    if hits:
        print(f"retrieval hit@k: {sum(hits)}/{len(hits)}")
    failed = [r["id"] for r in results if not r["answer_pass"]]
    print(f"failed: {failed if failed else 'none'}")


def save(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"eval-{stamp}-limit{args.limit}.json"
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved full results to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8, help="chunks retrieved")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--type", choices=("fact", "unknown", "refuse"))
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
