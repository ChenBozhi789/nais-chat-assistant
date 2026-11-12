# NAIS RAG Chunk Review Prompt

## Role and goal

Review all chunks from **one NAIS source page** before they are indexed in Azure AI Search. The chunks will support a website assistant answering visitor questions about topics such as opening hours, workshops, free AI tools, bookings, and tutorials. Judge whether each chunk can be retrieved and used to give a grounded answer.

Review date: `{{REVIEW_DATE_YYYY-MM-DD}}`

## Input

You will receive every chunk from one source page. Each chunk has a **chunk ID**, **token count**, **heading path**, and **text**. A page title, URL, source date, or full original page may also be supplied. Treat only supplied material as evidence.

```text
{{CHUNKS_FROM_ONE_SOURCE_PAGE}}
```

## Review each chunk

Score every dimension from **1 to 5**: 1 = unusable, 2 = major problem, 3 = mixed or uncertain, 4 = minor problem, 5 = ready to index. Give each score a **one-line reason** supported by an **exact quote** from the chunk or its heading path. When the issue is missing information, use an empty evidence string and say what is missing. Do not paraphrase inside the evidence field.

| Dimension | Check |
| --- | --- |
| `self_contained` | Can this chunk be understood alone? Flag unresolved pronouns, “above/below,” missing subjects, or context supplied only by another chunk. |
| `single_topic` | Does it cover one coherent topic? Flag mixed topics and heading-only fragments. |
| `retrievable` | Would a natural visitor question match its wording? Is the heading descriptive, and does the text contain the terms needed to find the answer? |
| `clean` | Is it free of navigation, footer text, UI badges such as “New” or “Start here,” tracking URLs, page numbers, and repeated boilerplate? |
| `accurate_current` | Are there visible typos, conflicting claims, or dates, prices, schedules, and availability that may have expired? A claim cannot be verified against the real world from chunks alone; mark that uncertainty rather than asserting it is true. |

Choose **one primary verdict** per chunk:

- `KEEP`: ready to index; state any minor optional edit.
- `MERGE_WITH`: too dependent on or duplicative of another chunk; give that chunk's ID and say what the merged text should contain.
- `SPLIT`: contains separable topics; specify the split points and proposed headings.
- `REWRITE`: can be fixed within one chunk; include the **complete rewritten chunk text**, using only supplied facts.
- `DROP`: irrelevant, empty, boilerplate, or redundant with no useful unique content; explain why.

Make every fix concrete. Do not use `REWRITE` to add facts that are absent from the input. If a fix needs source verification, say exactly what must be checked.

## Page-level checks

After reviewing all chunks, provide a short bullet list covering:

1. **Contradictions:** conflicting claims across chunk IDs, with exact quotes. Say `none found` if there are none in the supplied chunks.
2. **Duplication:** repeated information and the affected chunk IDs, with exact quotes. Say `none found` if applicable.
3. **Missing content:** important content absent from all chunks. If the original page was not supplied, you cannot know what it contains; say `uncertain` and identify only gaps suggested by the supplied headings or text.
4. **Stale facts:** time-sensitive claims, exact quotes, and metadata that would allow refresh or expiry (for example, `event_date`, `last_verified_at`, or `expires_at`). Do not invent dates.
5. **Question coverage:** five realistic visitor questions this page should answer, each paired with the chunk ID that supports its answer, plus two questions it should **not** answer from this page. If fewer than five supported questions are possible, list only those supported and explicitly state the shortfall; do not invent coverage.

## Output format

First output **valid JSON** for per-chunk results, in input order. Use one object per chunk with this structure; `merge_with` is a chunk ID only for `MERGE_WITH`, and `rewritten_text` is required only for `REWRITE`. Use `null` otherwise.

```json
{
  "chunks": [
    {
      "chunk_id": "<input chunk ID>",
      "scores": {
        "self_contained": { "score": 1, "reason": "<one line>", "evidence": "<exact quote or empty string>" },
        "single_topic": { "score": 1, "reason": "<one line>", "evidence": "<exact quote or empty string>" },
        "retrievable": { "score": 1, "reason": "<one line>", "evidence": "<exact quote or empty string>" },
        "clean": { "score": 1, "reason": "<one line>", "evidence": "<exact quote or empty string>" },
        "accurate_current": { "score": 1, "reason": "<one line>", "evidence": "<exact quote or empty string>" }
      },
      "verdict": "KEEP | MERGE_WITH | SPLIT | REWRITE | DROP",
      "merge_with": null,
      "fix": "<specific action or 'None'>",
      "rewritten_text": null
    }
  ]
}
```

Then output the page-level checks as a **short Markdown bullet list**. Keep quotes exact, identify relevant chunk IDs, and write `uncertain` wherever the supplied evidence cannot support a conclusion. Do not use outside knowledge to fill gaps or claim that a real-world fact is current.
