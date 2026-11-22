"""One error type for every refusal the tools make."""

from __future__ import annotations


class ToolError(Exception):
    """A request the server refuses. ``code`` is stable; ``message`` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message