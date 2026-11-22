"""Preview chunks from curated NAIS tutorials (PDF originals).

Markdown is read from ``content/nais-website/clean/curated-tutorials``.
A file is included only when the same stem exists as a PDF in
``content/nais-website/raw/curated-tutorials``.

Run from the repository root: ``python -m rag.chunk_curated_tutorials``.
"""

from backend.config import ROOT_DIR

from .chunk_documents import run_preview

SOURCE_DIR = ROOT_DIR / "content/nais-website/clean/curated-tutorials"
RAW_DIR = ROOT_DIR / "content/nais-website/raw/curated-tutorials"


def main() -> None:
    run_preview(
        ".pdf",
        "curated-tutorials",
        __doc__ or "Tutorial chunk preview",
        source_dir=SOURCE_DIR,
        raw_dir=RAW_DIR,
    )


if __name__ == "__main__":
    main()
