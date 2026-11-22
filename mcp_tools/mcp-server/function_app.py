"""Azure Functions remote MCP server: two read-only tools over NAIS Markdown files.

MCP endpoint (Streamable HTTP): ``/runtime/webhooks/mcp``. It is protected by the
``mcp_extension`` system key (header ``x-functions-key``); it is never anonymous.

The tool name is the Python function name, the docstring is the description the
model reads, and the type hints become the argument schema.
"""

import logging
from collections.abc import Callable
from typing import Any

import azure.functions as func

import documents as docs
import search
from errors import ToolError

app = func.FunctionApp()
logger = logging.getLogger("nais.mcp")


def _run(operation: Callable[[Any, int], object]) -> str:
    """Run a tool body; refusals become a JSON error the agent can read."""
    try:
        settings = docs.load_settings()
        return docs.to_json(operation(docs.default_container_client(), settings.max_bytes))
    except ToolError as exc:
        logger.warning("tool refused: code=%s", exc.code)
        return docs.error_json(exc)
    except Exception as exc:  # noqa: BLE001 - never leak internals or credentials
        logger.error("tool failed: %s", type(exc).__name__)
        return docs.error_json(ToolError("internal_error", "Unexpected server error."))


def _search(queries: list[str]) -> str:
    return _run(
        lambda container, max_bytes: search.search_in(
            docs.load_all(container, max_bytes), queries
        )
    )


def _read(filename: str) -> str:
    return _run(lambda container, max_bytes: docs.read_document(container, filename, max_bytes))


@app.mcp_tool()
@app.mcp_tool_property(
    arg_name="queries",
    description="1-6 short phrases; include synonyms where useful, for example ['event', 'workshop', 'training'].",
    as_array=True,  # required for list arguments: the decorator's default (False) overrides the type hint
)
def search_documents(queries: list[str]) -> str:
    """Search the full text of every approved NAIS Markdown file. Supply short phrases for each part of the question and alternate wording when needed; returns ranked filename, line, brief context and recommended files. Read the complete recommended files before answering."""
    return _search(queries)


@app.mcp_tool()
@app.mcp_tool_property(
    arg_name="filename",
    description="Exact .md file name from search_documents (see available_filenames), e.g. 'book-a-session.md'.",
)
def read_document(filename: str) -> str:
    """Return the complete, unchanged text of one approved NAIS document with its filename and etag."""
    return _read(filename)