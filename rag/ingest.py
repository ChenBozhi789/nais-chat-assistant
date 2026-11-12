"""Validate reviewed JSONL and optionally upload it to Azure AI Search.

Run from the repo root: ``python -m rag.ingest`` (local check) or
``python -m rag.ingest --upload`` (Azure embedding, index creation, upload).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.config import ROOT_DIR, settings

DEFAULT_INPUT = ROOT_DIR / "content/chunks/basic-pages/chunks.jsonl"
REQUIRED_FIELDS = ("id", "title", "url", "source_file", "section", "content")


def new_foundry_client() -> Any:
    """Use a Foundry deployment's OpenAI-compatible inference endpoint."""
    from openai import OpenAI

    missing = settings.missing_embedding_settings()
    if missing:
        raise ValueError(f"Missing .env settings: {', '.join(missing)}")
    parsed = urlparse(settings.foundry_openai_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path.rstrip("/") != "/openai/v1"
    ):
        raise ValueError(
            "FOUNDRY_OPENAI_BASE_URL must be an HTTPS inference URL ending "
            "in /openai/v1/ (not a Foundry project URL)"
        )
    return OpenAI(
        base_url=settings.foundry_openai_base_url,
        api_key=settings.foundry_api_key,
        timeout=60.0,
    )


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


def create_or_check_index(index_client: Any, dimensions: int) -> None:
    """Create a new index, or check an existing index before writing to it."""
    from azure.core.exceptions import ResourceNotFoundError
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    try:
        existing = index_client.get_index(settings.azure_search_index)
    except ResourceNotFoundError:
        existing = None

    if existing is not None:
        fields = {field.name: field for field in existing.fields}
        missing = {*REQUIRED_FIELDS, "embedding"} - fields.keys()
        if missing:
            raise ValueError(f"Existing index is missing fields: {sorted(missing)}")
        if fields["embedding"].vector_search_dimensions != dimensions:
            raise ValueError(
                "Existing index embedding dimensions differ from the model output. "
                "Use a new index name or the original embedding deployment."
            )
        print(f"Using existing index: {settings.azure_search_index}")
        return

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SimpleField(name="url", type=SearchFieldDataType.String),
        SimpleField(name="source_file", type=SearchFieldDataType.String),
        SearchableField(name="section", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="nais-vector-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="nais-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="nais-vector-profile", algorithm_configuration_name="nais-hnsw"
            )
        ],
    )
    index_client.create_index(
        SearchIndex(
            name=settings.azure_search_index,
            fields=fields,
            vector_search=vector_search,
        )
    )
    print(f"Created index: {settings.azure_search_index} ({dimensions} dimensions)")


def upload_chunks(chunks: list[dict[str, Any]], batch_size: int) -> None:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    missing = settings.missing_ingest_settings()
    if missing:
        raise ValueError(f"Missing .env settings: {', '.join(missing)}")

    openai_client = new_foundry_client()
    documents: list[dict[str, Any]] = []
    dimensions: int | None = None
    try:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            response = openai_client.embeddings.create(
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
                    raise ValueError("Embedding dimensions changed within this upload")
                documents.append(
                    {
                        **{field: chunk[field] for field in REQUIRED_FIELDS},
                        "embedding": vector,
                    }
                )
            print(f"Embedded {len(documents)}/{len(chunks)} chunks")
    finally:
        openai_client.close()

    if dimensions is None:
        raise ValueError("No embeddings created")
    credential = AzureKeyCredential(settings.azure_search_admin_key)
    with SearchIndexClient(settings.azure_search_endpoint, credential) as index_client:
        create_or_check_index(index_client, dimensions)
    with SearchClient(
        settings.azure_search_endpoint, settings.azure_search_index, credential
    ) as search_client:
        for start in range(0, len(documents), batch_size):
            results = search_client.upload_documents(
                documents=documents[start : start + batch_size]
            )
            failures = [result for result in results if not result.succeeded]
            if failures:
                details = ", ".join(
                    f"{result.key}: {result.error_message}" for result in failures
                )
                raise ValueError(f"Upload failed for {len(failures)} chunks: {details}")
            print(
                f"Uploaded {min(start + batch_size, len(documents))}/{len(documents)} chunks"
            )


def probe_embedding(content: str) -> None:
    """Test one Foundry embedding call without creating a Search index."""
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
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--upload", action="store_true", help="Embed and write to Azure"
    )
    action.add_argument(
        "--probe-embedding",
        action="store_true",
        help="Embed one chunk to verify the Foundry model; do not write to Search",
    )
    parser.add_argument(
        "--allow-flagged", action="store_true", help="Upload chunks with review_flags"
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
        if not args.upload:
            print("Local validation only. Add --probe-embedding or --upload.")
            return
        if flagged and not args.allow_flagged:
            raise ValueError("Review flagged chunks or use --allow-flagged explicitly")
        upload_chunks(chunks, args.batch_size)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
