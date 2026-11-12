"""Shared chunking and review helpers for the two NAIS content previews.

These helpers only write local previews; they do not call Azure or embed text.
Use ``backend.chunk_basic_pages`` or ``backend.chunk_curated_tutorials``.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import tiktoken

from .config import ROOT_DIR, settings

PAGE = re.compile(r"^## Page (\d+)[ \t]*$", re.MULTILINE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
TRAILING_URL = re.compile(r"\n\s*\n(https?://\S+)\s*$")
DIVIDER = re.compile(
    r"^(?:PART\s+\w+|Section\s+\d+|Agenda|Curated Stream\s*\d*)$", re.I
)


@dataclass
class SourceDocument:
    path: Path
    title: str
    url: str
    body: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class Section:
    headings: list[str]
    body: str
    label: str
    page: int | None = None
    page_end: int | None = None
    kind: str = "section"

    def content(self) -> str:
        return "\n\n".join([*self.headings, self.body]).strip()


def token_count(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text))


def load_document(path: Path) -> SourceDocument:
    lines = path.read_text(encoding="utf-8").splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return SourceDocument(path, path.stem, "", "", ["empty_file"])

    url = ""
    warnings: list[str] = []
    candidate = lines[first].strip()
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        url = candidate
        del lines[first]
    else:
        warnings.append("missing_source_url")

    body = "\n".join(lines).strip()
    title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    if not title_match:
        warnings.append("missing_title")
    else:
        body = (body[: title_match.start()] + body[title_match.end() :]).strip()

    # Trailing headings without content are source cleanup issues, not chunks.
    while body:
        last_line = body.splitlines()[-1]
        if not re.match(r"^#{1,6}[ \t]+\S", last_line):
            break
        body = "\n".join(body.splitlines()[:-1]).strip()
        warnings.append("empty_trailing_heading_removed")

    return SourceDocument(path, title, url, body, warnings)


def _has_substance(text: str) -> bool:
    return bool(re.sub(r"(?m)^#{1,6}\s+[^\n]*$", "", text).strip())


def _whole_document(doc: SourceDocument) -> list[Section]:
    return (
        [Section([f"# {doc.title}"], doc.body, doc.title, kind="whole")]
        if _has_substance(doc.body)
        else []
    )


def _page_sections(
    doc: SourceDocument, encoding: tiktoken.Encoding, maximum: int
) -> list[Section]:
    matches = list(PAGE.finditer(doc.body))
    sections: list[Section] = []
    intro = doc.body[: matches[0].start()].strip()
    if _has_substance(intro):
        sections.append(Section([f"# {doc.title}"], intro, "Introduction"))

    pages: list[Section] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.body)
        text = doc.body[match.end() : end].strip()
        if not _has_substance(text):
            doc.warnings.append(f"empty_page_{match.group(1)}")
            continue
        subheading = re.search(r"^###\s+(.+)$", text, re.MULTILINE)
        label = subheading.group(1).strip() if subheading else f"Page {match.group(1)}"
        number = int(match.group(1))
        pages.append(
            Section(
                [f"# {doc.title}"],
                f"## Page {number}\n\n{text}",
                label,
                number,
                number,
                "page",
            )
        )

    # Consecutive slides often contain only a few lines. Pack them within a
    # topic, while keeping section dividers as boundaries between groups.
    group: list[Section] = []
    for page in pages:
        divider = bool(DIVIDER.fullmatch(page.label))
        proposed = "\n\n".join(item.body for item in [*group, page])
        too_large = token_count(f"# {doc.title}\n\n{proposed}", encoding) > maximum
        if group and (divider or too_large):
            sections.append(_merge_pages(doc.title, group))
            group = []
        group.append(page)
    if group:
        sections.append(_merge_pages(doc.title, group))
    return sections


def _merge_pages(title: str, pages: list[Section]) -> Section:
    start = pages[0].page
    end = pages[-1].page
    label = pages[0].label if start == end else f"Pages {start}–{end}"
    return Section(
        [f"# {title}"],
        "\n\n".join(page.body for page in pages),
        label,
        start,
        end,
        "page",
    )


def _heading_sections(doc: SourceDocument, level: int) -> list[Section]:
    sections: list[Section] = []
    parent = ""
    label = ""
    body_lines: list[str] = []

    def flush(preserve_parent: bool = False) -> None:
        body = "\n".join(body_lines).strip()
        if not _has_substance(body):
            if not label:
                if (
                    preserve_parent
                    and parent
                    and parent.casefold() != doc.title.casefold()
                ):
                    doc.warnings.append(f"heading_only_review:{parent}")
                    sections.append(
                        Section([f"# {doc.title}", f"## {parent}"], "", parent)
                    )
                return
            # A heading can itself contain a useful fact (for example, an
            # address). Preserve it and ask for review instead of losing it.
            doc.warnings.append(f"heading_only_review:{label}")
        headings = [f"# {doc.title}"]
        if level == 3 and parent:
            headings.append(f"## {parent}")
        if label:
            headings.append(f"{'#' * level} {label}")
        kind = "item" if level == 3 and label else "section"
        sections.append(
            Section(headings, body, label or parent or "Introduction", kind=kind)
        )

    for line in doc.body.splitlines():
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)\s*$", line)
        if heading and len(heading.group(1)) == level:
            flush()
            label = heading.group(2).strip()
            body_lines = []
        elif heading and level == 3 and len(heading.group(1)) == 2:
            flush(preserve_parent=True)
            parent = heading.group(2).strip()
            label = ""
            body_lines = []
        else:
            body_lines.append(line)
    flush(preserve_parent=True)
    return sections


def make_sections(
    doc: SourceDocument, encoding: tiktoken.Encoding, maximum: int
) -> list[Section]:
    if not doc.body:
        return []
    if PAGE.search(doc.body):
        return _page_sections(doc, encoding, maximum)

    h3_count = len(re.findall(r"^###\s+", doc.body, re.MULTILINE))
    h2_count = len(re.findall(r"^##\s+", doc.body, re.MULTILINE))
    if h3_count >= max(2, 2 * h2_count):
        # Lists of workshops, tutorials, and resources have independent entries.
        sections = _heading_sections(doc, 3)
        # A heading-only introduction gives a search result no usable answer.
        # Keep its wording as context for the first actual list item instead.
        if (
            len(sections) >= 2
            and sections[0].kind == "section"
            and not sections[0].body
            and sections[1].kind == "item"
        ):
            intro, first_item = sections[:2]
            first_item.headings = [
                intro.headings[0],
                *intro.headings[1:],
                *first_item.headings[1:],
            ]
            sections = sections[1:]
            warning = f"heading_only_review:{intro.label}"
            if warning in doc.warnings:
                doc.warnings.remove(warning)
        if len(sections) >= 2 and all(
            section.kind == "section" for section in sections[:2]
        ):
            first, second = sections[:2]
            parent_heading = "\n\n".join(second.headings[1:])
            combined = Section(
                [f"# {doc.title}"],
                f"{first.body}\n\n{parent_heading}\n\n{second.body}",
                "Introduction",
            )
            if token_count(combined.content(), encoding) <= maximum:
                sections = [combined, *sections[2:]]
        return sections

    whole = _whole_document(doc)
    if whole and token_count(whole[0].content(), encoding) <= maximum:
        return whole
    return _heading_sections(doc, 2)


def _split_large_unit(unit: str, budget: int, encoding: tiktoken.Encoding) -> list[str]:
    if token_count(unit, encoding) <= budget:
        return [unit]

    sentences = SENTENCE_END.split(unit)
    if len(sentences) > 1:
        pieces: list[str] = []
        for sentence in sentences:
            pieces.extend(_split_large_unit(sentence, budget, encoding))
        return pieces

    words = unit.split()
    if len(words) > 1:
        pieces = []
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if current and token_count(trial, encoding) > budget:
                pieces.append(current)
                current = word
            else:
                current = trial
            if token_count(current, encoding) > budget:
                pieces.extend(_split_large_unit(current, budget, encoding))
                current = ""
        if current:
            pieces.append(current)
        return pieces

    # Last resort for an oversized URL or uninterrupted word.
    pieces = []
    remaining = unit
    while remaining:
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if token_count(remaining[:middle], encoding) <= budget:
                low = middle
            else:
                high = middle - 1
        pieces.append(remaining[:low])
        remaining = remaining[low:]
    return pieces


def _overlap_tail(text: str, limit: int, encoding: tiktoken.Encoding) -> str:
    if limit <= 0:
        return ""
    words = text.split()
    tail: list[str] = []
    for word in reversed(words):
        trial = " ".join([word, *tail])
        if token_count(trial, encoding) > limit:
            break
        tail.insert(0, word)
    return " ".join(tail)


def split_section(
    section: Section, maximum: int, overlap: int, encoding: tiktoken.Encoding
) -> list[str]:
    full = section.content()
    if token_count(full, encoding) <= maximum:
        return [full]

    prefix = "\n\n".join(section.headings)
    budget = maximum - token_count(prefix + "\n\n", encoding)
    if budget <= 0:
        raise ValueError(f"Heading exceeds the token limit: {section.label}")

    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", section.body):
        paragraph = paragraph.strip()
        if paragraph:
            units.extend(_split_large_unit(paragraph, budget, encoding))

    bodies: list[str] = []
    current = ""
    for unit in units:
        trial = "\n\n".join(filter(None, [current, unit]))
        if current and token_count(trial, encoding) > budget:
            bodies.append(current)
            tail = _overlap_tail(current, overlap, encoding)
            current = "\n\n".join([tail, unit]) if tail else unit
            if token_count(current, encoding) > budget:
                current = unit
        else:
            current = trial
    if current:
        bodies.append(current)
    return [f"{prefix}\n\n{body}" for body in bodies]


def make_chunks(
    doc: SourceDocument,
    maximum: int,
    overlap: int,
    encoding: tiktoken.Encoding,
) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    for section in make_sections(doc, encoding, maximum):
        for content in split_section(section, maximum, overlap, encoding):
            # A list item ending in a bare URL (e.g. a curated tutorial) keeps
            # that link as metadata instead of in the embedded text.
            tutorial_url = ""
            if section.kind == "item":
                link = TRAILING_URL.search(content)
                if link:
                    tutorial_url = link.group(1)
                    content = content[: link.start()].rstrip()
            count = token_count(content, encoding)
            flags = []
            if count < (25 if section.kind == "item" else 80):
                flags.append("short_chunk_review")
            if not doc.url:
                flags.append("missing_source_url")
            chunks.append(
                {
                    "id": f"{doc.path.stem}-{len(chunks) + 1}",
                    "source_file": doc.path.name,
                    "title": doc.title,
                    "url": doc.url,
                    "section": section.label,
                    "page": section.page,
                    "page_end": section.page_end,
                    "content": content,
                    "token_count": count,
                    "review_flags": flags,
                    **({"tutorial_url": tutorial_url} if tutorial_url else {}),
                }
            )
    if not chunks:
        doc.warnings.append("no_chunks")
    return chunks


def write_report(
    path: Path,
    data_path: Path,
    documents: list[SourceDocument],
    chunks: list[dict[str, object]],
) -> None:
    by_file: dict[str, list[dict[str, object]]] = {}
    for chunk in chunks:
        by_file.setdefault(str(chunk["source_file"]), []).append(chunk)
    line_by_id = {str(chunk["id"]): line for line, chunk in enumerate(chunks, 1)}

    parts = [
        "<!doctype html><html lang='en'><meta charset='utf-8'>",
        "<title>NAIS chunk review</title>",
        "<style>body{font:16px/1.5 system-ui;max-width:1000px;"
        "margin:2rem auto;padding:0 1rem}"
        "details{border:1px solid #bbb;border-radius:8px;"
        "margin:1rem 0;padding:1rem}"
        "article{border-top:1px solid #ddd;padding:1rem 0}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;"
        "background:#f5f5f5;padding:1rem}"
        "mark{background:#ffe08a;padding:.1rem .3rem}"
        "small{color:#444}</style><body>",
        f"<h1>NAIS chunk review</h1><p>{len(documents)} files · {len(chunks)} chunks. "
        "Review flagged chunks and source links before indexing.</p>",
        "<p><strong>Chunk data to edit:</strong> "
        f"<a href='chunks.jsonl'>chunks.jsonl</a> "
        f"<code>{html.escape(str(data_path))}</code></p>",
        "<p>Each line is one JSON chunk. Use the line number below or search for "
        "its chunk ID, then edit its <code>content</code> field. Running the "
        "chunk command again will overwrite manual changes in this file.</p>",
    ]
    for doc in documents:
        items = by_file.get(doc.path.name, [])
        warnings = ", ".join(doc.warnings)
        summary = f"{doc.path.name} — {len(items)} chunks"
        if warnings:
            summary += " ⚠"
        parts.append(f"<details><summary>{html.escape(summary)}</summary>")
        if doc.url:
            safe_url = html.escape(doc.url, quote=True)
            parts.append(f"<p>Source: <a href='{safe_url}'>{safe_url}</a></p>")
        else:
            parts.append("<p>Source: (missing)</p>")
        if warnings:
            parts.append(
                f"<p><mark>Document warnings: {html.escape(warnings)}</mark></p>"
            )
        for chunk in items:
            flags = ", ".join(chunk["review_flags"])
            parts.append(
                f"<article><h3>{html.escape(str(chunk['id']))}</h3>"
                f"<small>JSONL line {line_by_id[str(chunk['id'])]} · "
                f"{chunk['token_count']} tokens · "
                f"{html.escape(str(chunk['section']))}</small>"
            )
            if flags:
                parts.append(f"<p><mark>{html.escape(flags)}</mark></p>")
            parts.append(f"<pre>{html.escape(str(chunk['content']))}</pre></article>")
        parts.append("</details>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def run_preview(
    raw_extension: str,
    output_name: str,
    description: str,
    raw_subdir: str = "",
) -> None:
    """Preview one content group, using raw file types to classify clean Markdown."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--source", type=Path, default=ROOT_DIR / "content/clean")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT_DIR / "content/chunks" / output_name
    )
    parser.add_argument("--max-tokens", type=int, default=settings.chunk_tokens)
    parser.add_argument(
        "--overlap-tokens", type=int, default=settings.chunk_overlap_tokens
    )
    parser.add_argument("--encoding", default="cl100k_base")
    args = parser.parse_args()
    if args.max_tokens <= 0 or not 0 <= args.overlap_tokens < args.max_tokens:
        parser.error("Require max-tokens > 0 and 0 <= overlap-tokens < max-tokens")
    if not args.source.exists():
        parser.error(f"Source path does not exist: {args.source}")

    encoding = tiktoken.get_encoding(args.encoding)
    if args.source.is_dir():
        candidates = sorted(args.source.glob("*.md"))
    elif args.source.suffix.lower() == ".md":
        candidates = [args.source]
    else:
        parser.error("Source must be a Markdown file or directory")
    raw_dir = ROOT_DIR / "content/raw" / raw_subdir
    paths = [
        path
        for path in candidates
        if (raw_dir / f"{path.stem}{raw_extension}").is_file()
    ]
    if args.source.is_file() and not paths:
        parser.error(
            f"{args.source.name} does not have a matching {raw_extension} source "
            f"in {raw_dir}"
        )
    if not paths:
        parser.error(
            f"No Markdown files with matching {raw_extension} sources "
            f"found in {args.source}"
        )

    documents = [load_document(path) for path in paths]
    chunks = [
        chunk
        for doc in documents
        for chunk in make_chunks(doc, args.max_tokens, args.overlap_tokens, encoding)
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "chunks.jsonl"
    report = args.output_dir / "chunk-review.html"
    output.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
    write_report(report, output, documents, chunks)
    flagged = sum(bool(chunk["review_flags"]) for chunk in chunks)
    warnings = sum(len(doc.warnings) for doc in documents)
    print(
        f"{len(documents)} files → {len(chunks)} chunks "
        f"({flagged} flagged chunks, {warnings} document warnings)"
    )
    print(f"Data: {output}\nReview: {report}")
