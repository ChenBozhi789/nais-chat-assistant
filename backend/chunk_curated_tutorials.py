"""Preview chunks from curated NAIS tutorials (PDF originals).

Run from the repository root: ``python -m backend.chunk_curated_tutorials``.
"""

from .chunk_documents import run_preview


def main() -> None:
    run_preview(
        ".pdf",
        "curated-tutorial",
        __doc__ or "Tutorial chunk preview",
        raw_subdir="curated-tutorials",
    )


if __name__ == "__main__":
    main()
