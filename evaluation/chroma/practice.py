"""Learn Chroma with the repository's already embedded NAIS documents.

Run from the repository root: .venv/bin/python evaluation/chroma/practice.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

ROOT = Path(__file__).resolve().parents[2]
# SOURCE_DIR = ROOT / "content/embedding/nais-content-basic-pages"
SOURCE_DIR = ROOT / "content/embedding/nais-content-curated-tutorials"
DB_DIR = Path(__file__).resolve().parent / "db"
COLLECTION_NAME = "nais-chroma-practice"
BATCH_SIZE = 32


def read_export() -> tuple[list[dict[str, Any]], int]:
    """Check the backup before putting any of its records into Chroma."""
    manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    raw = (SOURCE_DIR / "documents.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["documents_sha256"]:
        raise ValueError("Export checksum does not match manifest.json")

    dimensions = manifest["embedding_dimensions"]
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(records) != manifest["documents"]:
        raise ValueError("Export document count does not match manifest.json")

    ids: set[str] = set()
    for record in records:
        vector = record.get("embedding")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in ids:
            raise ValueError("Export contains a missing or duplicate ID")
        if not isinstance(record.get("content"), str) or not record["content"]:
            raise ValueError(f"Document {record_id} has no content")
        if (
            not isinstance(vector, list)
            or len(vector) != dimensions
            or any(
                not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector
            )
        ):
            raise ValueError(f"Document {record_id} has an invalid embedding")
        ids.add(record_id)
    return records, dimensions


def open_collection() -> Any:
    """Keep this exercise in its own persistent local database."""
    client = chromadb.PersistentClient(path=str(DB_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )


def ingest() -> None:
    records, dimensions = read_export()
    collection = open_collection()
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        collection.upsert(
            ids=[item["id"] for item in batch],
            documents=[item["content"] for item in batch],
            embeddings=[item["embedding"] for item in batch],
            metadatas=[
                {
                    key: item[key]
                    for key in ("title", "url", "source_file", "section")
                    if isinstance(item.get(key), str) and item[key]
                }
                for item in batch
            ],
        )
    print(f"Imported {len(records)} records ({dimensions} dimensions each).")
    print(f"Collection now contains {collection.count()} records in {DB_DIR}")


def inspect() -> None:
    collection = open_collection()
    print(f"Collection: {collection.name}; records: {collection.count()}")
    sample = collection.get(limit=3, include=["metadatas", "documents"])
    for record_id, metadata, document in zip(
        sample["ids"], sample["metadatas"] or [], sample["documents"] or []
    ):
        print(f"- {record_id}: {(metadata or {}).get('title', '(untitled)')}")
        print(f"  {(document or '')[:120].replace(chr(10), ' ')}")


def query_vector(vector: list[float], limit: int) -> None:
    collection = open_collection()
    if collection.count() == 0:
        raise ValueError("Collection is empty; run 'ingest' first")
    result = collection.query(
        query_embeddings=[vector],
        n_results=limit,
        include=["documents", "metadatas", "distances"],
    )
    for record_id, document, metadata, distance in zip(
        result["ids"][0],
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0],
    ):
        source = metadata or {}
        print(f"\n{record_id} | cosine distance={distance:.4f}")
        print(f"{source.get('title', '(untitled)')} | {source.get('url', '')}")
        print((document or "")[:280].replace("\n", " "))


def similar_to_id(record_id: str, limit: int) -> None:
    collection = open_collection()
    record = collection.get(ids=[record_id], include=["embeddings"])
    if not record["ids"]:
        raise ValueError(f"ID not found: {record_id}")
    # get() returns a NumPy array for embeddings; convert one row to a plain list.
    vector = record["embeddings"][0].tolist()
    query_vector(vector, limit)


def make_client(*deployment_vars: str) -> OpenAI:
    """Build a Foundry client after checking the required .env values."""
    load_dotenv(ROOT / ".env")
    names = ("FOUNDRY_OPENAI_BASE_URL", "FOUNDRY_API_KEY", *deployment_vars)
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError(f"Set these values in .env first: {', '.join(missing)}")
    return OpenAI(
        base_url=os.environ["FOUNDRY_OPENAI_BASE_URL"],
        api_key=os.environ["FOUNDRY_API_KEY"],
        timeout=60.0,
        max_retries=0,
    )


def embed_question(client: OpenAI, question: str) -> list[float]:
    """Embed the question with the same model used for the stored vectors."""
    try:
        response = client.embeddings.create(
            model=os.environ["FOUNDRY_EMBEDDING_DEPLOYMENT"], input=question
        )
    except OpenAIError as exc:
        raise RuntimeError(
            "Foundry embedding request failed; check endpoint and deployment"
        ) from exc
    vector = response.data[0].embedding
    _, dimensions = read_export()
    if len(vector) != dimensions:
        raise ValueError(
            f"Query has {len(vector)} dimensions; stored vectors have {dimensions}"
        )
    return vector


def ask(question: str, limit: int) -> None:
    """Retrieval only: print the closest chunks."""
    if open_collection().count() == 0:
        raise ValueError("Collection is empty; run 'ingest' first")
    client = make_client("FOUNDRY_EMBEDDING_DEPLOYMENT")
    query_vector(embed_question(client, question), limit)


SYSTEM_PROMPT = (
    "You are the NAIS (Nelson AI Sandbox) assistant. Answer ONLY from the "
    "provided context. Be concise and friendly. If the context does not "
    "contain the answer, say you don't know and suggest contacting NAIS. "
    "Never invent details about NAIS."
)


def generate_answer(
    client: OpenAI, question: str, chunks: list[dict[str, Any]]
) -> str:
    """Send the retrieved chunks plus the question to the Foundry chat model."""
    context = "\n\n".join(
        f"[{i}] {c['title']} ({c['url']})\n{c['content']}"
        for i, c in enumerate(chunks, start=1)
    )
    try:
        response = client.chat.completions.create(
            model=os.environ["FOUNDRY_CHAT_DEPLOYMENT"],
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {question}",
                },
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(
            "Foundry chat request failed; check FOUNDRY_CHAT_DEPLOYMENT"
        ) from exc
    return response.choices[0].message.content or ""


def answer(question: str, limit: int) -> None:
    """Full RAG: embed, retrieve, then generate a grounded answer."""
    collection = open_collection()
    if collection.count() == 0:
        raise ValueError("Collection is empty; run 'ingest' first")
    client = make_client("FOUNDRY_EMBEDDING_DEPLOYMENT", "FOUNDRY_CHAT_DEPLOYMENT")
    result = collection.query(
        query_embeddings=[embed_question(client, question)],
        n_results=limit,
        include=["documents", "metadatas"],
    )
    chunks = [
        {
            "title": (meta or {}).get("title", "(untitled)"),
            "url": (meta or {}).get("url", ""),
            "content": doc or "",
        }
        for doc, meta in zip(result["documents"][0], result["metadatas"][0])
    ]
    print(generate_answer(client, question, chunks))
    print("\nSources:")
    for url in dict.fromkeys(c["url"] for c in chunks if c["url"]):
        print(f"- {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="Import existing vectors into local Chroma")
    commands.add_parser("inspect", help="Show count and three stored documents")
    similar = commands.add_parser(
        "similar", help="Search using one stored document's vector"
    )
    similar.add_argument("id", help="Existing document ID")
    question = commands.add_parser(
        "ask", help="Embed a question with Foundry, then search"
    )
    question.add_argument("text", help="Question text")
    rag = commands.add_parser(
        "answer", help="Retrieve chunks, then ask the Foundry chat model"
    )
    rag.add_argument("text", help="Question text")
    for command in (similar, question, rag):
        command.add_argument("--limit", type=int, default=4)
    args = parser.parse_args()
    if args.command == "ingest":
        ingest()
    elif args.command == "inspect":
        inspect()
    elif args.command == "similar":
        similar_to_id(args.id, args.limit)
    elif args.command == "answer":
        answer(args.text, args.limit)
    else:
        ask(args.text, args.limit)


if __name__ == "__main__":
    main()
