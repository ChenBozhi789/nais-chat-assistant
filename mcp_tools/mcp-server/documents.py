"""Read-only access to approved NAIS Markdown files in a private blob container.

The container client is passed in, so tests never touch Azure. Files are returned
byte-for-byte (decoded as UTF-8). Document contents are never logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceModifiedError,
    ResourceNotFoundError,
)

from errors import ToolError

logger = logging.getLogger("nais.mcp.documents")

DEFAULT_MAX_BYTES = 1_048_576  # 1 MiB; the largest NAIS page is ~15 KB

# A flat, exact file name: no separators, no leading dot, lower-case .md only.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$")


@dataclass(frozen=True)
class Settings:
    account_url: str
    container: str
    max_bytes: int


def load_settings() -> Settings:
    """Read configuration from the environment (App Settings in Azure)."""
    account_url = os.getenv("NAIS_DOCS_STORAGE_ACCOUNT_URL", "").strip()
    container = os.getenv("NAIS_DOCS_CONTAINER", "").strip()
    missing = [
        name
        for name, value in {
            "NAIS_DOCS_STORAGE_ACCOUNT_URL": account_url,
            "NAIS_DOCS_CONTAINER": container,
        }.items()
        if not value
    ]
    if missing:
        raise ToolError("not_configured", f"Missing settings: {', '.join(missing)}")
    raw_max = os.getenv("NAIS_DOCS_MAX_BYTES", "").strip()
    try:
        max_bytes = int(raw_max) if raw_max else DEFAULT_MAX_BYTES
    except ValueError as exc:
        raise ToolError("not_configured", "NAIS_DOCS_MAX_BYTES must be an integer") from exc
    if max_bytes <= 0:
        raise ToolError("not_configured", "NAIS_DOCS_MAX_BYTES must be positive")
    return Settings(account_url, container, max_bytes)


@lru_cache(maxsize=1)
def default_container_client() -> Any:
    """Container client using the Function App's managed identity (created once)."""
    # Imported lazily so unit tests and tool discovery never need credentials.
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient

    settings = load_settings()
    return ContainerClient(
        account_url=settings.account_url,
        container_name=settings.container,
        credential=DefaultAzureCredential(),
    )


def is_valid_filename(filename: object) -> bool:
    return (
        isinstance(filename, str)
        and ".." not in filename
        and _FILENAME_RE.fullmatch(filename) is not None
    )


def list_documents(container: Any, max_bytes: int) -> list[dict[str, Any]]:
    """Approved documents: top-level ``*.md`` blobs no larger than ``max_bytes``."""
    documents: list[dict[str, Any]] = []
    try:
        for blob in container.list_blobs():
            if not is_valid_filename(blob.name) or blob.size > max_bytes:
                continue
            documents.append(
                {
                    "filename": blob.name,
                    "size_bytes": blob.size,
                    "etag": blob.etag,
                    "last_modified": blob.last_modified.isoformat() if blob.last_modified else None,
                }
            )
    except HttpResponseError as exc:
        logger.error("list failed: status=%s", getattr(exc, "status_code", None))
        raise ToolError("storage_error", "Could not list documents.") from exc
    documents.sort(key=lambda d: d["filename"])
    logger.info("list_documents: count=%d", len(documents))
    return documents


def read_document(container: Any, filename: object, max_bytes: int) -> dict[str, Any]:
    """Return one document's exact text with its filename and ETag."""
    if not is_valid_filename(filename):
        raise ToolError(
            "invalid_filename",
            "filename must be an exact name from search_documents (a .md file, no paths).",
        )
    assert isinstance(filename, str)
    blob = container.get_blob_client(filename)
    try:
        props = blob.get_blob_properties()
        if props.size > max_bytes:
            raise ToolError("too_large", f"{filename} exceeds the size limit.")
        # Pin to the ETag we checked so the size check and the bytes agree.
        stream = blob.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified)
        data: bytes = stream.readall()
        etag = props.etag
    except ResourceNotFoundError as exc:
        raise ToolError("not_found", f"{filename} was not found.") from exc
    except ResourceModifiedError as exc:
        raise ToolError("changed", f"{filename} changed while reading; retry.") from exc
    except HttpResponseError as exc:
        logger.error("read failed: file=%s status=%s", filename, getattr(exc, "status_code", None))
        raise ToolError("storage_error", f"Could not read {filename}.") from exc

    if len(data) > max_bytes:
        raise ToolError("too_large", f"{filename} exceeds the size limit.")
    try:
        text = data.decode("utf-8")  # strict; a BOM, if any, is preserved
    except UnicodeDecodeError as exc:
        raise ToolError("not_utf8", f"{filename} is not valid UTF-8.") from exc
    logger.info("read_document: file=%s bytes=%d", filename, len(data))
    return {"filename": filename, "etag": etag, "size_bytes": len(data), "text": text}


# (filename, etag) -> lines. A changed file has a new ETag, so it is read again.
_lines_cache: dict[tuple[str, str], list[str]] = {}


def load_all(container: Any, max_bytes: int) -> list[tuple[str, list[str]]]:
    """Every approved document as ``(filename, lines)``, re-reading only changed files."""
    loaded: list[tuple[str, list[str]]] = []
    for item in list_documents(container, max_bytes):
        key = (item["filename"], item["etag"])
        if key not in _lines_cache:
            doc = read_document(container, item["filename"], max_bytes)
            _lines_cache[key] = doc["text"].splitlines()
        loaded.append((item["filename"], _lines_cache[key]))
    return loaded


def to_json(payload: object) -> str:
    """Serialise a tool result. JSON round-trips the text losslessly."""
    return json.dumps(payload, ensure_ascii=False)


def error_json(exc: ToolError) -> str:
    return to_json({"error": exc.code, "message": exc.message})