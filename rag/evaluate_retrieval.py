"""Batch-check Azure AI Search retrieval with evaluation/questions.json.

Run from the repository root, for example::

    python -m rag.evaluate_retrieval --only loc hours
    python -m rag.evaluate_retrieval

This checks retrieved evidence, not generated answers. Unknown/refuse cases are
included for inspection but cannot be graded until the chat API exists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.config import ROOT_DIR, settings
from rag.ingest import new_foundry_client

QUESTIONS_PATH = ROOT_DIR / "evaluation/questions.json"
DEFAULT_OUTPUT = ROOT_DIR / "evaluation/retrieval-results.json"


def load_questions(
    path: Path, ids: set[str], case_type: str | None
) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Questions file must contain a JSON array")
    selected = [
        case
        for case in cases
        if (not ids or case.get("id") in ids)
        and (case_type is None or case.get("type") == case_type)
    ]
    if not selected:
        raise ValueError("No questions match the selected IDs/type")
    for case in selected:
        if not isinstance(case.get("id"), str) or not isinstance(case.get("q"), str):
            raise ValueError("Each selected question needs string id and q fields")
    return selected


def embed_questions(
    cases: list[dict[str, Any]], batch_size: int = 16
) -> list[list[float]]:
    vectors: list[list[float]] = []
    client = new_foundry_client()
    try:
        for start in range(0, len(cases), batch_size):
            batch = cases[start : start + batch_size]
            response = client.embeddings.create(
                model=settings.foundry_embedding_deployment,
                input=[case["q"] for case in batch],
            )
            batch_vectors = [
                item.embedding for item in sorted(response.data, key=lambda x: x.index)
            ]
            if len(batch_vectors) != len(batch):
                raise ValueError("Embedding response count differs from question count")
            vectors.extend(batch_vectors)
    finally:
        client.close()
    return vectors


def normalized_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def assess_fact(case: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    expected_urls = {normalized_url(url) for url in case.get("expected_urls", [])}
    source_found = any(normalized_url(hit["url"]) in expected_urls for hit in hits)
    context = "\n".join(hit["content"] for hit in hits).casefold()
    missing_facts = [
        fact
        for fact in case.get("expected_facts", [])
        if fact.casefold() not in context
    ]
    expected_any = case.get("expected_any", [])
    any_found = not expected_any or any(
        term.casefold() in context for term in expected_any
    )
    return {
        "source_found": source_found,
        "missing_facts": missing_facts,
        "expected_any_found": any_found,
        "status": (
            "PASS" if source_found and not missing_facts and any_found else "REVIEW"
        ),
    }


def search_cases(
    cases: list[dict[str, Any]], vectors: list[list[float]] | None, top: int
) -> list[dict[str, Any]]:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.models import VectorizedQuery

    key = settings.azure_search_query_key or settings.azure_search_admin_key
    missing = [
        name
        for name, value in (
            ("AZURE_SEARCH_ENDPOINT", settings.azure_search_endpoint),
            ("AZURE_SEARCH_INDEX", settings.azure_search_index),
            ("AZURE_SEARCH_QUERY_KEY or AZURE_SEARCH_ADMIN_KEY", key),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing .env settings: {', '.join(missing)}")

    results: list[dict[str, Any]] = []
    with SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index,
        credential=AzureKeyCredential(key),
    ) as search_client:
        for position, case in enumerate(cases):
            options: dict[str, Any] = {}
            if vectors is not None:
                options["vector_queries"] = [
                    VectorizedQuery(
                        vector=vectors[position],
                        k_nearest_neighbors=top,
                        fields="embedding",
                    )
                ]
            try:
                response = search_client.search(
                    search_text=case["q"],
                    select=["id", "title", "url", "content"],
                    top=top,
                    **options,
                )
                hits = [
                    {
                        "id": hit.get("id", ""),
                        "title": hit.get("title", ""),
                        "url": hit.get("url", ""),
                        "content": hit.get("content", ""),
                        "score": hit.get("@search.score"),
                    }
                    for hit in response
                ]
                assessment = (
                    assess_fact(case, hits)
                    if case["type"] == "fact"
                    else {"status": "INSPECT", "reason": "Requires a generated answer"}
                )
                results.append(
                    {
                        "id": case["id"],
                        "type": case["type"],
                        "q": case["q"],
                        "assessment": assessment,
                        "hits": hits,
                    }
                )
            # Keep a per-question report when one request fails.
            except Exception as exc:
                detail = str(exc).splitlines()
                error = f"{type(exc).__name__}: {detail[0][:200] if detail else ''}"
                results.append(
                    {
                        "id": case["id"],
                        "type": case["type"],
                        "q": case["q"],
                        "assessment": {"status": "ERROR"},
                        "error": error,
                        "hits": [],
                    }
                )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", nargs="+", metavar="ID", help="Run selected question IDs"
    )
    parser.add_argument("--type", choices=("fact", "unknown", "refuse"))
    parser.add_argument("--keyword-only", action="store_true", help="Skip embeddings")
    parser.add_argument("--top", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.top <= 0:
        parser.error("--top must be positive")

    try:
        cases = load_questions(QUESTIONS_PATH, set(args.only or []), args.type)
        vectors = None if args.keyword_only else embed_questions(cases)
        results = search_cases(cases, vectors, args.top)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    counts = {
        status: sum(row["assessment"]["status"] == status for row in results)
        for status in ("PASS", "REVIEW", "INSPECT", "ERROR")
    }
    report = {
        "mode": "keyword" if args.keyword_only else "hybrid",
        "index": settings.azure_search_index,
        "top": args.top,
        "counts": counts,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for row in results:
        first = row["hits"][0]["id"] if row["hits"] else "—"
        print(f"{row['assessment']['status']:7} {row['id']:18} top1={first}")
        if row["assessment"]["status"] == "ERROR":
            print(f"  {row['error']}")
    print(f"Summary: {counts}; report: {args.output}")
    if counts["ERROR"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
