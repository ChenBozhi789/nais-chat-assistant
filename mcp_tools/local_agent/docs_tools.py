"""Read and search the local NAIS Markdown documents without an index."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from backend.config import ROOT_DIR

DEFAULT_DOCS_DIR = "content/nais-website/clean/zip"
DEFAULT_MAX_BYTES = 1_048_576  # 1 MiB
MAX_SEARCH_QUERIES = 6
MAX_QUERY_CHARS = 100
MAX_HITS_PER_QUERY = 30
CONTEXT_CHARS = 300
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$")
WORD_RE = re.compile(r"[a-z0-9]+")
SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "can",
    "do",
    "for",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "where",
    "without",
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


def docs_dir() -> Path:
    """Document directory (``LOCAL_DOCS_DIR``, relative to the repo root)."""
    p = Path(os.getenv("LOCAL_DOCS_DIR", DEFAULT_DOCS_DIR))
    return p if p.is_absolute() else ROOT_DIR / p


def _error(code: str, message: str) -> str:
    return json.dumps({"error": code, "message": message})


def _documents(base: Path, max_bytes: int) -> list[Path]:
    """Use the same top-level allowlist for listing, searching and reading."""
    return [
        p
        for p in sorted(base.iterdir())
        if p.is_file()
        and not p.is_symlink()
        and FILENAME_RE.fullmatch(p.name)
        and p.stat().st_size <= max_bytes
    ]


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


def search_documents(
    queries: Any, directory: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES
) -> str:
    """Search all allowed Markdown files with phrase and term overlap matching."""
    if (
        not isinstance(queries, list)
        or not 1 <= len(queries) <= MAX_SEARCH_QUERIES
        or any(
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_QUERY_CHARS
            for query in queries
        )
    ):
        return _error(
            "invalid_queries",
            f"Supply 1-{MAX_SEARCH_QUERIES} nonempty literal search phrases "
            f"of at most {MAX_QUERY_CHARS} characters each.",
        )
    base = directory or docs_dir()
    if not base.is_dir():
        return _error("not_configured", "Document directory does not exist.")

    documents: list[tuple[str, list[str]]] = []
    for path in _documents(base, max_bytes):
        try:
            documents.append((path.name, path.read_text(encoding="utf-8").splitlines()))
        except UnicodeDecodeError:
            continue

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
    return json.dumps(
        {
            "documents_scanned": len(documents),
            "results": results,
            "recommended_files": recommended,
            "available_filenames": [filename for filename, _ in documents],
        },
        ensure_ascii=False,
    )


def read_document(
    filename: Any, directory: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES
) -> str:
    """Complete, unchanged UTF-8 text of one document."""
    if not isinstance(filename, str) or not FILENAME_RE.match(filename):
        return _error("invalid_filename", "Use an exact .md name from search_documents.")
    path = (directory or docs_dir()) / filename
    if not path.is_file() or path.is_symlink():
        return _error("not_found", "No such document.")
    size = path.stat().st_size
    if size > max_bytes:
        return _error("too_large", "Document exceeds the size limit.")
    try:
        # newline="" keeps CRLF etc. untouched.
        with path.open("r", encoding="utf-8", newline="") as f:
            text = f.read()
    except UnicodeDecodeError:
        return _error("not_utf8", "Document is not valid UTF-8.")
    return json.dumps({"filename": filename, "size_bytes": size, "text": text})


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the full text of every approved NAIS Markdown file. "
                "Supply short phrases for each part of the question and "
                "alternate wording when needed; returns ranked filename, line, "
                "brief context and recommended files. Read the complete "
                "recommended files before answering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": MAX_SEARCH_QUERIES,
                        "description": (
                            "1-6 short phrases; include synonyms where useful, "
                            "for example ['event', 'workshop', 'training']."
                        ),
                    }
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Return the complete, unchanged text of one NAIS document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": (
                            "Exact .md file name from search_documents "
                            "(see available_filenames), e.g. 'book-a-session.md'."
                        ),
                    }
                },
                "required": ["filename"],
            },
        },
    },
]


def dispatch(name: str, arguments_json: str) -> str:
    """Run a tool by name; unknown tools or bad arguments return error JSON."""
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        return _error("bad_arguments", "Arguments must be valid JSON.")
    if not isinstance(args, dict):
        return _error("bad_arguments", "Arguments must be a JSON object.")
    if name == "search_documents":
        return search_documents(args.get("queries"))
    if name == "read_document":
        return read_document(args.get("filename"))
    return _error("unknown_tool", f"Unknown tool: {name}")
