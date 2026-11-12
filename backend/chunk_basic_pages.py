"""Preview chunks from basic NAIS pages (HTML originals).

Run from the repository root: ``python -m backend.chunk_basic_pages``.
"""

from .chunk_documents import run_preview


def main() -> None:
    run_preview(".html", "basic-pages", __doc__ or "Basic page chunk preview")


if __name__ == "__main__":
    main()
