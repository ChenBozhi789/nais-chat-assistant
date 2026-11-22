# tests/conftest.py
"""Fake blob container so tests never touch Azure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError


@dataclass
class FakeBlob:
    name: str
    size: int
    etag: str
    last_modified: datetime | None = datetime(2026, 1, 2, tzinfo=UTC)


class FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class FakeBlobClient:
    def __init__(self, container: "FakeContainer", name: str) -> None:
        self._c, self._name = container, name

    def get_blob_properties(self) -> FakeBlob:
        if self._name not in self._c.store:
            raise ResourceNotFoundError("missing")
        data = self._c.store[self._name]
        return FakeBlob(self._name, len(data), f'"etag-{self._name}-{len(data)}"')

    def download_blob(self, **kwargs: Any) -> FakeStream:
        assert kwargs["match_condition"] == MatchConditions.IfNotModified
        self._c.downloads.append(self._name)
        return FakeStream(self._c.store[self._name])


class FakeContainer:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store
        self.downloads: list[str] = []

    def list_blobs(self) -> list[FakeBlob]:
        return [
            FakeBlob(n, len(b), f'"etag-{n}-{len(b)}"') for n, b in self.store.items()
        ]

    def get_blob_client(self, name: str) -> FakeBlobClient:
        return FakeBlobClient(self, name)


@pytest.fixture
def container() -> FakeContainer:
    return FakeContainer(
        {
            "home.md": b"https://x.test/home\nFree 45-minute sessions\nDrop in or book a time\n",
            "book-a-session.md": b"https://x.test/book\nBook a free session\nYou can drop in without booking\n",
            "rata.md": "https://x.test/rata\nRātā workshops for community groups\n".encode(),
            "notes.txt": b"not approved",
            "Bad.MD": b"not approved",
        }
    )