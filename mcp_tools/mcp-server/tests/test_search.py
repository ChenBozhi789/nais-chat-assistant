
# tests/test_search.py
import pytest

import search
from errors import ToolError

DOCS = [
    ("home.md", ["Free 45-minute sessions", "Drop in or book a time"]),
    ("book-a-session.md", ["Book a free session", "You can drop in without booking"]),
    ("rata.md", ["Rātā workshops for community groups"]),
]


def test_phrase_hit_with_context():
    out = search.search_in(DOCS, ["45-minute"])
    hit = out["results"][0]["hits"][0]
    assert hit["filename"] == "home.md" and hit["line"] == 1
    assert out["documents_scanned"] == 3


def test_accent_folding():
    out = search.search_in(DOCS, ["rata"])
    assert out["results"][0]["hits"][0]["filename"] == "rata.md"


def test_booking_question_recommends_booking_page_first():
    out = search.search_in(DOCS, ["book a session"])
    assert out["recommended_files"][0] == "book-a-session.md"


@pytest.mark.parametrize("bad", [None, "x", [], [""], [5], ["a"] * 7, ["x" * 101]])
def test_invalid_queries(bad):
    with pytest.raises(ToolError) as exc:
        search.search_in(DOCS, bad)
    assert exc.value.code == "invalid_queries"