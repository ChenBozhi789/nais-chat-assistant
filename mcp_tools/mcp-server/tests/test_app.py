# tests/test_app.py
import json

import documents as docs
import function_app

# get_functions() may only be called once per app, so cache the result.
_FUNCTIONS = {f.get_function_name(): f for f in function_app.app.get_functions()}


def _functions():
    return _FUNCTIONS


def test_exactly_two_tools_registered():
    assert sorted(_functions()) == ["read_document", "search_documents"]


def _props(name):
    binding = _functions()[name].get_bindings()[0]
    return json.loads(binding.get_dict_repr()["toolProperties"])


def test_search_queries_is_a_required_string_array():
    (prop,) = _props("search_documents")
    assert prop["propertyName"] == "queries"
    assert prop["propertyType"] == "string"
    assert prop["isArray"] is True
    assert prop["isRequired"] is True


def test_read_filename_is_a_required_string():
    (prop,) = _props("read_document")
    assert (prop["propertyName"], prop["propertyType"], prop["isArray"]) == ("filename", "string", False)


def test_tools_return_json_and_refusals(container, monkeypatch):
    monkeypatch.setenv("NAIS_DOCS_STORAGE_ACCOUNT_URL", "https://a.blob.core.windows.net")
    monkeypatch.setenv("NAIS_DOCS_CONTAINER", "c")
    monkeypatch.setattr(docs, "default_container_client", lambda: container)
    docs._lines_cache.clear()

    found = json.loads(function_app._search(["book a session"]))
    assert found["recommended_files"][0] == "book-a-session.md"

    doc = json.loads(function_app._read("home.md"))
    assert doc["filename"] == "home.md" and "45-minute" in doc["text"]

    assert json.loads(function_app._read("../x.md"))["error"] == "invalid_filename"
    assert json.loads(function_app._search([]))["error"] == "invalid_queries"


def test_unexpected_failures_do_not_leak_details(monkeypatch):
    monkeypatch.setenv("NAIS_DOCS_STORAGE_ACCOUNT_URL", "https://a.blob.core.windows.net")
    monkeypatch.setenv("NAIS_DOCS_CONTAINER", "c")

    def boom():
        raise RuntimeError("secret connection string")

    monkeypatch.setattr(docs, "default_container_client", boom)
    out = function_app._read("home.md")
    assert json.loads(out) == {"error": "internal_error", "message": "Unexpected server error."}
    assert "secret" not in out