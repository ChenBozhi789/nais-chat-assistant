"""Full-text search over already-loaded documents. Pure Python: no Azure, no MCP.

Input is a list of ``(filename, lines)``. Output is a plain dict that the tool
turns into JSON. Matching is by phrase, then by overlapping words, with accent
folding (Rata = Rata with a macron), simple plurals and a few synonyms.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from errors import ToolError

MAX_SEARCH_QUERIES = 6
MAX_QUERY_CHARS = 100
MAX_HITS_PER_QUERY = 30
CONTEXT_CHARS = 300
WORD_RE = re.compile(r"[a-z0-9]+")
SEARCH_STOPWORDS = {
    "a", "an", "and", "at", "can", "do", "for", "in", "is", "of", "on",
    "or", "the", "to", "what", "where", "without",
}
FILENAME_GENERIC_WORDS = {"ai", "free", "nais", "nelson", "sandbox"}
QUERY_GENERIC_WORDS = FILENAME_GENERIC_WORDS | {"help"}
QUERY_SYNONYMS = {
    "reserve": {"book", "booking", "secure", "schedule", "appointment"},
    "reservation": {"book", "booking", "secure", "schedule", "appointment"},
    "cost": {"free", "price", "fee", "charge"},
    "price": {"free", "cost", "fee", "charge"},
    "duration": {"minute", "hour", "day"},
    "length": {"minute", "hour", "day"},
    "long": {"minute", "hour", "day"},
    "location": {"address", "street", "road", "located"},
    "address": {"location", "street", "road", "located"},
    "recent": {"latest", "upcoming", "current", "new"},
}


def _normalise(value: str) -> str:
    """Match case and accent variants such as Rata and Rātā."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _term_matches(term: str, words: set[str], expand: bool = True) -> bool:
    """Accept simple inflections (session/sessions, book/booking)."""
    variants = {term, *(QUERY_SYNONYMS.get(term, set()) if expand else set())}
    for variant in variants:
        for word in words:
            if variant == word:
                return True
            shorter, longer = sorted((variant, word), key=len)
            if len(shorter) >= 4 and longer.startswith(shorter):
                if longer[len(shorter) :] in {"s", "es", "ing", "ed", "ly"}:
                    return True
    return False


def _line_score(query: str, line: str) -> int:
    """Prefer a phrase match, then overlapping terms in one source line."""
    needle = _normalise(query.strip())
    haystack = _normalise(line)
    if needle in haystack:
        return 100 + len(needle)
    terms = [word for word in WORD_RE.findall(needle) if word not in SEARCH_STOPWORDS]
    if not terms:
        return 0
    words = set(WORD_RE.findall(haystack))
    distinct_terms = set(terms)
    distinctive = distinct_terms - QUERY_GENERIC_WORDS
    if distinctive and not any(_term_matches(term, words) for term in distinctive):
        return 0
    matched = sum(_term_matches(term, words) for term in distinct_terms)
    if matched < min(2, len(distinct_terms)) and not (
        len(distinctive) == 1 and matched == 1
    ):
        return 0
    if {"duration", "long", "length"} & set(terms):
        if re.search(r"\b\d+[- ]?(?:minute|hour)s?\b", haystack):
            matched += 3
    return matched


def check_queries(queries: Any) -> list[str]:
    """Return the queries if valid, else raise a ToolError the model can read."""
    if (
        not isinstance(queries, list)
        or not 1 <= len(queries) <= MAX_SEARCH_QUERIES
        or any(
            not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY_CHARS
            for q in queries
        )
    ):
        raise ToolError(
            "invalid_queries",
            f"Supply 1-{MAX_SEARCH_QUERIES} nonempty search phrases "
            f"of at most {MAX_QUERY_CHARS} characters each.",
        )
    return queries


def search_in(
    documents: list[tuple[str, list[str]]], queries: Any
) -> dict[str, Any]:
    """Search ``documents`` for each query; rank files and recommend which to read."""
    check_queries(queries)

    results: list[dict[str, Any]] = []
    for query in queries:
        hits: list[dict[str, Any]] = []
        query_words = (
            set(WORD_RE.findall(_normalise(query)))
            - SEARCH_STOPWORDS
            - FILENAME_GENERIC_WORDS
        )
        for filename, lines in documents:
            best: dict[str, Any] | None = None
            filename_words = set(WORD_RE.findall(_normalise(filename)))
            name_bonus = 2 * sum(
                _term_matches(
                    term,
                    filename_words,
                    expand=term in {"reserve", "reservation"},
                )
                for term in query_words
            )
            for index, line in enumerate(lines):
                score = _line_score(query, line)
                if not score:
                    continue
                score += name_bonus
                if best is not None and score <= best["score"]:
                    continue
                nearby = " ".join(lines[max(0, index - 1) : index + 2])
                best = {
                    "filename": filename,
                    "line": index + 1,
                    "context": " ".join(nearby.split())[:CONTEXT_CHARS],
                    "score": score,
                }
            if best is not None:
                hits.append(best)
        hits.sort(key=lambda hit: (-hit["score"], hit["filename"]))
        results.append({"query": query, "hits": hits[:MAX_HITS_PER_QUERY]})

    coverage: dict[str, tuple[int, int]] = {}
    for result in results:
        for rank, hit in enumerate(result["hits"]):
            name = hit["filename"]
            count, score = coverage.get(name, (0, 0))
            coverage[name] = (count + 1, score + hit["score"] - rank)
    ranked = sorted(
        coverage, key=lambda name: (-coverage[name][0], -coverage[name][1], name)
    )
    strong_results = [
        result
        for result in results
        if len(
            [
                word
                for word in WORD_RE.findall(_normalise(result["query"]))
                if word not in SEARCH_STOPWORDS
            ]
        )
        >= 2
    ]
    recommended: list[str] = []
    for result in strong_results:
        if result["hits"]:
            name = result["hits"][0]["filename"]
            if name not in recommended:
                recommended.append(name)
    limit = min(3, max(2, len(strong_results) + 1))
    if len(strong_results) == 1 and recommended:
        if coverage[recommended[0]][0] == len(results):
            limit = 1
    for name in ranked:
        if len(recommended) >= limit:
            break
        if name not in recommended:
            recommended.append(name)
    recommended = recommended[:limit]
    return {
        "documents_scanned": len(documents),
        "results": results,
        "recommended_files": recommended,
        "available_filenames": [filename for filename, _ in documents],
    }