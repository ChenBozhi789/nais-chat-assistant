"""Validate reviewed JSONL and embed it locally with a Foundry deployment.

Run from the repo root: ``python -m rag.embed_chunks`` (local check) or
``python -m rag.embed_chunks --embed`` (Foundry embedding, written to local files).
Nothing is sent to Azure AI Search; the output can be loaded into Chroma.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.config import ROOT_DIR, settings
from rag.foundry_client import new_foundry_client

DEFAULT_INPUT = ROOT_DIR / "content/chunks/basic-pages/chunks.jsonl"
DEFAULT_OUTPUT = ROOT_DIR / "content/embedding/basic-pages"
REQUIRED_FIELDS = ("id", "title", "url", "source_file", "section", "content")


def load_chunks(path: Path) -> list[dict[str, Any]]:
    """Read the exact reviewed chunk file; never regenerate its content."""
    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(chunk, dict):
            raise TypeError(f"{path}:{number}: expected a JSON object")
        for field in REQUIRED_FIELDS:
            if not isinstance(chunk.get(field), str) or not chunk[field].strip():
                raise ValueError(f"{path}:{number}: missing or empty {field}")
        if chunk["id"] in seen_ids:
            raise ValueError(f"{path}:{number}: duplicate id {chunk['id']}")
        seen_ids.add(chunk["id"])
        parsed = urlparse(chunk["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{path}:{number}: invalid source URL")
        if not isinstance(chunk.get("review_flags", []), list):
            raise TypeError(f"{path}:{number}: review_flags must be a list")
        chunks.append(chunk)
    if not chunks:
        raise ValueError(f"No chunks found in {path}")
    return chunks


def embed_chunks(chunks: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    """Embed every chunk's content; return documents with an ``embedding`` field."""
    client = new_foundry_client()
    documents: list[dict[str, Any]] = []
    dimensions: int | None = None
    try:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            response = client.embeddings.create(
                model=settings.foundry_embedding_deployment,
                input=[chunk["content"] for chunk in batch],
            )
            vectors = [
                item.embedding for item in sorted(response.data, key=lambda x: x.index)
            ]
            if len(vectors) != len(batch):
                raise ValueError("Embedding response count differs from chunk count")
            for chunk, vector in zip(batch, vectors, strict=True):
                if dimensions is None:
                    dimensions = len(vector)
                elif len(vector) != dimensions:
                    raise ValueError("Embedding dimensions changed within this run")
                documents.append(
                    {
                        **{field: chunk[field] for field in REQUIRED_FIELDS},
                        "embedding": vector,
                    }
                )
            print(f"Embedded {len(documents)}/{len(chunks)} chunks")
    finally:
        client.close()
    return documents


def write_embeddings(documents: list[dict[str, Any]], output_dir: Path) -> None:
    """Write documents.jsonl and manifest.json (same layout as the Search export)."""
    os.umask(0o077)  # The output contains source text and full vectors.
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = "".join(
        json.dumps(doc, ensure_ascii=False) + "\n" for doc in documents
    ).encode("utf-8")
    (output_dir / "documents.jsonl").write_bytes(raw)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "documents": len(documents),
        "embedding_dimensions": len(documents[0]["embedding"]),
        "embedding_deployment": settings.foundry_embedding_deployment,
        "documents_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(documents)} embedded chunks to {output_dir}")


def probe_embedding(content: str) -> None:
    """Test one Foundry embedding call without writing any files."""
    client = new_foundry_client()
    try:
        response = client.embeddings.create(
            model=settings.foundry_embedding_deployment,
            input=content,
        )
        print(
            f"Foundry embedding returned {len(response.data[0].embedding)} dimensions"
        )
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for documents.jsonl and manifest.json",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--embed",
        action="store_true",
        help="Embed all chunks with Foundry and write local files",
    )
    action.add_argument(
        "--probe-embedding",
        action="store_true",
        help="Embed one chunk to verify the Foundry model; write nothing",
    )
    parser.add_argument(
        "--allow-flagged", action="store_true", help="Embed chunks with review_flags"
    )
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if not args.input.is_file():
        parser.error(f"Input file not found: {args.input}")
    try:
        chunks = load_chunks(args.input)
        flagged = [chunk["id"] for chunk in chunks if chunk.get("review_flags")]
        print(f"Validated {len(chunks)} chunks in {args.input}")
        if flagged:
            print(f"Review flags on {len(flagged)} chunks: {', '.join(flagged)}")
        if args.probe_embedding:
            probe_embedding(chunks[0]["content"])
            return
        if not args.embed:
            print("Local validation only. Add --probe-embedding or --embed.")
            return
        if flagged and not args.allow_flagged:
            raise ValueError("Review flagged chunks or use --allow-flagged explicitly")
        write_embeddings(embed_chunks(chunks, args.batch_size), args.output_dir)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
