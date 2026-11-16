"""Preview chunks from basic NAIS pages (HTML originals).

Markdown is read from ``content/nais-website/clean/basic-pages``.
A file is included only when the same stem exists as HTML in
``content/nais-website/raw/basic-pages``.

Run from the repository root: ``python -m rag.chunk_basic_pages``.
"""

from backend.config import ROOT_DIR

from .chunk_documents import run_preview

SOURCE_DIR = ROOT_DIR / "content/nais-website/clean/basic-pages"
RAW_DIR = ROOT_DIR / "content/nais-website/raw/basic-pages"


def main() -> None:
    run_preview(
        ".html",
        "basic-pages",
        __doc__ or "Basic page chunk preview",
        source_dir=SOURCE_DIR,
        raw_dir=RAW_DIR,
    )


if __name__ == "__main__":
    main()
