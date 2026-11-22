"""Offline tests for the local document tools and agent loop."""

from __future__ import annotations

import json
from types import SimpleNamespace

from mcp_tools.local_agent import agent as agent_local
from mcp_tools.local_agent import docs_tools as t


def _docs(tmp_path):
    (tmp_path / "a.md").write_bytes("https://x.test/a\r\nHello\tworld é \r\n".encode())
    (tmp_path / "notes.txt").write_text("no")
    return tmp_path


def test_read_exact(tmp_path):
    d = _docs(tmp_path)
    doc = json.loads(t.read_document("a.md", d))
    assert doc["text"].encode() == (d / "a.md").read_bytes()


def test_rejections(tmp_path):
    d = _docs(tmp_path)
    for bad in ["../a.md", "notes.txt", "A.MD", ".a.md", 5]:
        assert json.loads(t.read_document(bad, d))["error"] == "invalid_filename"
    assert json.loads(t.read_document("missing.md", d))["error"] == "not_found"
    assert json.loads(t.read_document("a.md", d, max_bytes=3))["error"] == "too_large"


def test_searches_all_files_with_context_and_accent_folding(tmp_path):
    _docs(tmp_path)
    (tmp_path / "home.md").write_text(
        "Welcome\nFree 45-minute sessions\nDrop in or book a time\n"
    )
    (tmp_path / "rata.md").write_text("Rātā workshops for community groups\n")
    result = json.loads(t.search_documents(["45-minute", "rata"], tmp_path))
    assert result["documents_scanned"] == 3
    assert result["results"][0]["hits"] == [
        {
            "filename": "home.md",
            "line": 2,
            "context": "Welcome Free 45-minute sessions Drop in or book a time",
            "score": 109,
        }
    ]
    assert result["results"][1]["hits"][0]["filename"] == "rata.md"
    assert "notes.txt" not in result["available_filenames"]


def test_search_ranks_overlapping_source_files(tmp_path):
    (tmp_path / "home.md").write_text(
        "Free 45-minute sessions\nDrop in or book a time\nNelson Sandbox\n"
    )
    (tmp_path / "book-a-session.md").write_text(
        "Book a free session\nYou can drop in without booking\n"
    )
    (tmp_path / "about-us.md").write_text(
        "Nelson Sandbox at Halifax Cafe, 28 Halifax Street\n"
    )
    result = json.loads(
        t.search_documents(
            [
                "drop in without booking",
                "free session duration",
                "Nelson Sandbox location",
            ],
            tmp_path,
        )
    )
    assert "home.md" in result["recommended_files"]
    assert "about-us.md" in result["recommended_files"]
    assert any(hit["filename"] == "home.md" for hit in result["results"][1]["hits"])


def test_search_matches_booking_and_cost_wording(tmp_path):
    (tmp_path / "book-a-session.md").write_text(
        "Book a session in Nelson\nYou can drop in or book an appointment\n"
    )
    (tmp_path / "home.md").write_text("Free 45-minute sessions in Nelson\n")
    result = json.loads(
        t.search_documents(["Nelson reservation", "session cost"], tmp_path)
    )
    assert result["results"][0]["hits"][0]["filename"] == "book-a-session.md"
    assert any(hit["filename"] == "home.md" for hit in result["results"][1]["hits"])


def test_search_rejects_invalid_queries_and_symlink(tmp_path):
    _docs(tmp_path)
    (tmp_path / "linked.md").symlink_to(tmp_path / "a.md")
    for queries in [[], [""], ["x"] * 7, ["x" * 101], "hello"]:
        assert json.loads(t.search_documents(queries, tmp_path))["error"] == (
            "invalid_queries"
        )
    assert (
        "linked.md"
        not in json.loads(t.search_documents(["Hello"], tmp_path))[
            "available_filenames"
        ]
    )
    assert json.loads(t.read_document("linked.md", tmp_path))["error"] == "not_found"


def test_dispatch_errors():
    assert json.loads(t.dispatch("nope", "{}"))["error"] == "unknown_tool"
    assert (
        json.loads(t.dispatch("read_document", "not json"))["error"] == "bad_arguments"
    )


def test_multiline_sources_must_all_have_been_read():
    assert agent_local._valid_sources(
        "Answer.\nSources:\n- a.md\n- b.md", ["a.md", "b.md"]
    )
    assert not agent_local._valid_sources(
        "Answer.\nSources:\n- a.md\n- unread.md", ["a.md"]
    )


def _resp(content=None, calls=None):
    msg = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_agent_loop(tmp_path, monkeypatch):
    _docs(tmp_path)
    monkeypatch.setenv("LOCAL_DOCS_DIR", str(tmp_path))
    call = lambda i, n, a: SimpleNamespace(  # noqa: E731
        id=i, function=SimpleNamespace(name=n, arguments=a)
    )
    script = iter(
        [
            _resp(
                calls=[call("1", "search_documents", '{"queries": ["Hello", "world"]}')]
            ),
            _resp(calls=[call("2", "read_document", '{"filename": "a.md"}')]),
            _resp(content="Answer.\nSources: a.md"),
        ]
    )
    choices = []

    def create(**kwargs):
        choices.append(kwargs["tool_choice"])
        return next(script)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    answer, files = agent_local.run_agent("q", client=client, model="m", verbose=False)
    assert answer.startswith("Answer") and files == ["a.md"]
    assert [c["function"]["name"] for c in choices[:2]] == [
        "search_documents",
        "read_document",
    ]
    assert choices[2] == "auto"


def test_agent_reads_all_recommended_files_before_answering(tmp_path, monkeypatch):
    _docs(tmp_path)
    (tmp_path / "b.md").write_text("Hello from another source\n")
    monkeypatch.setenv("LOCAL_DOCS_DIR", str(tmp_path))
    call = lambda i, n, a: SimpleNamespace(  # noqa: E731
        id=i, function=SimpleNamespace(name=n, arguments=a)
    )
    script = iter(
        [
            _resp(calls=[call("1", "search_documents", '{"queries": ["Hello"]}')]),
            _resp(calls=[call("2", "read_document", '{"filename": "a.md"}')]),
            _resp(calls=[call("3", "read_document", '{"filename": "b.md"}')]),
            _resp(content="Answer.\nSources: a.md, b.md"),
        ]
    )
    choices = []

    def create(**kwargs):
        choices.append(kwargs["tool_choice"])
        return next(script)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    answer, files = agent_local.run_agent("q", client=client, model="m", verbose=False)
    assert answer.startswith("Answer") and files == ["a.md", "b.md"]
    assert [choice["function"]["name"] for choice in choices[:3]] == [
        "search_documents",
        "read_document",
        "read_document",
    ]
    assert choices[3] == "auto"


def test_agent_retries_empty_search_and_rejects_unread_source(tmp_path, monkeypatch):
    _docs(tmp_path)
    monkeypatch.setenv("LOCAL_DOCS_DIR", str(tmp_path))
    call = lambda i, n, a: SimpleNamespace(  # noqa: E731
        id=i, function=SimpleNamespace(name=n, arguments=a)
    )
    script = iter(
        [
            _resp(calls=[call("1", "search_documents", '{"queries": ["missing"]}')]),
            _resp(calls=[call("2", "search_documents", '{"queries": ["Hello"]}')]),
            _resp(calls=[call("3", "read_document", '{"filename": "a.md"}')]),
            _resp(content="Answer.\nSources: unread.md"),
            _resp(content="Answer.\nSources: a.md"),
        ]
    )
    choices = []

    def create(**kwargs):
        choices.append(kwargs["tool_choice"])
        return next(script)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    answer, files = agent_local.run_agent("q", client=client, model="m", verbose=False)
    assert answer.endswith("Sources: a.md") and files == ["a.md"]
    assert [c["function"]["name"] for c in choices[:3]] == [
        "search_documents",
        "search_documents",
        "read_document",
    ]
