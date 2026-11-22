# tests/test_documents.py
import json

import pytest

import documents as docs
from errors import ToolError

MAX = 1000


def test_list_only_approved_names(container):
    names = [d["filename"] for d in docs.list_documents(container, MAX)]
    assert names == ["book-a-session.md", "home.md", "rata.md"]


def test_list_skips_files_over_limit(container):
    container.store["big.md"] = b"x" * 2000
    names = [d["filename"] for d in docs.list_documents(container, MAX)]
    assert "big.md" not in names


def test_read_exact_text(container):
    doc = docs.read_document(container, "rata.md", MAX)
    assert doc["text"] == container.store["rata.md"].decode()
    assert doc["filename"] == "rata.md"


@pytest.mark.parametrize("bad", ["../a.md", "notes.txt", "A.MD", ".a.md", "a/b.md", 5, None])
def test_invalid_filenames(container, bad):
    with pytest.raises(ToolError) as exc:
        docs.read_document(container, bad, MAX)
    assert exc.value.code == "invalid_filename"


def test_missing_and_too_large_and_not_utf8(container):
    with pytest.raises(ToolError) as exc:
        docs.read_document(container, "missing.md", MAX)
    assert exc.value.code == "not_found"
    container.store["big.md"] = b"x" * 2000
    with pytest.raises(ToolError) as exc:
        docs.read_document(container, "big.md", MAX)
    assert exc.value.code == "too_large"
    container.store["bin.md"] = b"\xff\xfe\x00"
    with pytest.raises(ToolError) as exc:
        docs.read_document(container, "bin.md", MAX)
    assert exc.value.code == "not_utf8"


def test_load_all_downloads_once_per_etag(container):
    docs._lines_cache.clear()
    first = docs.load_all(container, MAX)
    assert [n for n, _ in first] == ["book-a-session.md", "home.md", "rata.md"]
    assert len(container.downloads) == 3
    docs.load_all(container, MAX)
    assert len(container.downloads) == 3  # cached
    container.store["home.md"] = b"changed\n"
    docs.load_all(container, MAX)
    assert container.downloads.count("home.md") == 2  # new ETag -> re-read


def test_settings(monkeypatch):
    monkeypatch.delenv("NAIS_DOCS_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("NAIS_DOCS_CONTAINER", raising=False)
    with pytest.raises(ToolError) as exc:
        docs.load_settings()
    assert exc.value.code == "not_configured"
    monkeypatch.setenv("NAIS_DOCS_STORAGE_ACCOUNT_URL", "https://a.blob.core.windows.net")
    monkeypatch.setenv("NAIS_DOCS_CONTAINER", "c")
    assert docs.load_settings().max_bytes == docs.DEFAULT_MAX_BYTES
    monkeypatch.setenv("NAIS_DOCS_MAX_BYTES", "abc")
    with pytest.raises(ToolError):
        docs.load_settings()


def test_error_json_shape():
    out = json.loads(docs.error_json(ToolError("x", "msg")))
    assert out == {"error": "x", "message": "msg"}