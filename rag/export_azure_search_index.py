"""Back up the configured Azure AI Search index, including its embeddings.

Run from the repository root: ``python -m rag.export_azure_search_index``.
The default operation only reads Azure Search. See ``--help`` for the optional
schema change needed when the embedding field is stored but not retrievable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient

from backend.config import ROOT_DIR, settings

EMBEDDING_FIELD = "embedding"


def require_search_settings() -> None:
    required = {
        "AZURE_SEARCH_ENDPOINT": settings.azure_search_endpoint,
        "AZURE_SEARCH_ADMIN_KEY": settings.azure_search_admin_key,
        "AZURE_SEARCH_INDEX": settings.azure_search_index,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing .env settings: {', '.join(missing)}")


def get_exportable_index(
    index_client: SearchIndexClient, enable_retrieval: bool
) -> Any:
    index = index_client.get_index(settings.azure_search_index)
    vector_fields = {
        field.name: field for field in index.fields if field.vector_search_dimensions
    }
    if EMBEDDING_FIELD not in vector_fields:
        raise ValueError(f"Index has no {EMBEDDING_FIELD!r} vector field")
    vector = vector_fields[EMBEDDING_FIELD]
    if vector.stored is False:
        raise ValueError(
            "The embedding field has stored=false. Its original vectors cannot "
            "be exported from Azure Search; keep the source chunks and re-embed later."
        )
    if not vector.retrievable:
        if not enable_retrieval:
            raise ValueError(
                "The embedding field is stored but not retrievable. Rerun with "
                "--enable-vector-retrieval to update that index field before export."
            )
        vector.retrievable = True
        index_client.create_or_update_index(index)
        index = index_client.get_index(settings.azure_search_index)
        vector = next(field for field in index.fields if field.name == EMBEDDING_FIELD)
        if not vector.retrievable:
            raise ValueError(
                "Embedding field is still not retrievable after the update"
            )
    return index


def export_documents(search_client: SearchClient, index: Any, path: Path) -> int:
    expected = search_client.get_document_count()
    fields = [field.name for field in index.fields if field.retrievable]
    key = next(field.name for field in index.fields if field.key)
    dimensions = next(
        field.vector_search_dimensions
        for field in index.fields
        if field.name == EMBEDDING_FIELD
    )
    seen: set[str] = set()
    count = 0
    results = search_client.search(search_text="*", select=fields)
    with path.open("w", encoding="utf-8") as output:
        for page in results.by_page():
            for result in page:
                document = {name: result[name] for name in fields if name in result}
                document_key = document.get(key)
                vector = document.get(EMBEDDING_FIELD)
                if not isinstance(document_key, str) or document_key in seen:
                    raise ValueError("A document has a missing or duplicate key")
                if not isinstance(vector, list) or len(vector) != dimensions:
                    raise ValueError(
                        f"Document {document_key!r} has a missing or invalid embedding"
                    )
                seen.add(document_key)
                output.write(
                    json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                )
                output.write("\n")
                count += 1
    after = search_client.get_document_count()
    if count != expected or count != after:
        raise ValueError(
            f"Document count changed or export was incomplete: "
            f"before={expected}, exported={count}, after={after}"
        )
    return count


def export_index(output_root: Path, enable_retrieval: bool) -> Path:
    require_search_settings()
    os.umask(0o077)  # The backup contains source text and full embedding vectors.
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_root / f"{settings.azure_search_index}-{timestamp}"
    if destination.exists():
        raise FileExistsError(f"Backup already exists: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=".search-export-", dir=output_root))
    credential = AzureKeyCredential(settings.azure_search_admin_key)
    try:
        with SearchIndexClient(
            settings.azure_search_endpoint, credential
        ) as index_client:
            index = get_exportable_index(index_client, enable_retrieval)
        schema_path = temporary / "index-schema.json"
        schema_path.write_text(
            json.dumps(index.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        documents_path = temporary / "documents.jsonl"
        with SearchClient(
            settings.azure_search_endpoint, settings.azure_search_index, credential
        ) as search_client:
            count = export_documents(search_client, index, documents_path)
        digest = hashlib.sha256(documents_path.read_bytes()).hexdigest()
        vector = next(field for field in index.fields if field.name == EMBEDDING_FIELD)
        manifest = {
            "index": index.name,
            "exported_at_utc": timestamp,
            "documents": count,
            "embedding_field": EMBEDDING_FIELD,
            "embedding_dimensions": vector.vector_search_dimensions,
            "documents_sha256": digest,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT_DIR / "content/exports",
        help="Directory for dated backup folders (default: content/exports)",
    )
    parser.add_argument(
        "--enable-vector-retrieval",
        action="store_true",
        help="Change the existing embedding field to retrievable before export",
    )
    args = parser.parse_args()
    try:
        destination = export_index(args.output_root, args.enable_vector_retrieval)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(f"Verified backup: {destination}")


if __name__ == "__main__":
    main()
