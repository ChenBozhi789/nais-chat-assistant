# nais-chat-assistant

MVP AI chat assistant for the Nelson AI Sandbox (NAIS) website. A floating widget embedded via a single script tag (React + TypeScript, Shadow DOM), a Python FastAPI backend, and RAG over approved NAIS documents using **Azure AI Search + Microsoft Foundry model deployments**.

> Status: local chunk review and an Azure AI Search upload command are available. Cloud indexing, answer retrieval, API, and widget UI have not yet been verified end to end.

## Prerequisites
- Python 3.11+ (3.12 recommended)
- Node 20+ and npm

## Local setup

```bash
cp .env.example .env            # then fill in the Azure values

# Backend
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m backend.config   # prints loaded settings, never secrets

# Widget
cd widget
npm install
npm run build                   # outputs widget/dist/widget.js
npm run lint
```

Open `widget/test.html` once the backend serves `/widget.js` (step 5).

## Preview document chunks

From the repo root, after installing the backend requirements:

```bash
.venv/bin/python -m rag.chunk_basic_pages
.venv/bin/python -m rag.chunk_curated_tutorials
```

The first command selects cleaned Markdown whose original is an HTML page in
`content/nais-website/raw/basic-pages/`; the second selects Markdown whose
original is a PDF in `content/nais-website/raw/curated-tutorials/`.
This keeps the `curated-streams` HTML index page with basic pages and the PDF
contents with tutorials. Each command writes `chunks.jsonl` and
`chunk-review.html` to its own directory under `content/chunks/`:
`basic-pages/` or `curated-tutorials/`. Open the HTML reports to inspect source
links, token counts, page ranges, and review flags. The files are regenerated
on each run and ignored by Git. Neither command makes Azure calls or creates
embeddings or a search index.

To edit a chunk manually, open `content/chunks/basic-pages/chunks.jsonl` or
`content/chunks/curated-tutorials/chunks.jsonl` in a text editor. Each line is
one JSON object. The HTML report now shows the JSONL path and line number for
every chunk; change only the `content` value on that line and keep the JSON
valid. Rerunning the corresponding chunk command overwrites manual edits.

To try one document first:

```bash
.venv/bin/python -m rag.chunk_basic_pages --source content/nais-website/clean/basic-pages/book-a-session.md
```

The default limit and overlap come from `CHUNK_TOKENS` and
`CHUNK_OVERLAP_TOKENS`. The tokenizer defaults to `cl100k_base`, appropriate
for a Foundry deployment of `text-embedding-3-small`; use `--encoding` if the embedding model changes.
`tiktoken` may download its encoding data on first use. The source URL on the
first line of each Markdown file is stored as metadata, and empty trailing
headings are reported and omitted from the chunk content. Review flagged
chunks before indexing.

## Layout
- `backend/` config, shared API code and three independent implementations. Each lives in its own folder with `answer.py`, `main.py` (entry point) and `tests/`:

  | Implementation | Folder | Run from the repo root |
  |---|---|---|
  | 1. Chat completions + Chroma RAG | `backend/impl1_chat_completions_chroma/` | `uvicorn backend.impl1_chat_completions_chroma.main:app` |
  | 2. Foundry agent + File Search | `backend/impl2_agent_file_search/` | `uvicorn backend.impl2_agent_file_search.main:app` |
  | 3. Foundry agent + remote MCP tools | `backend/impl3_agent_mcp/` | `uvicorn backend.impl3_agent_mcp.main:app` |

  Shared code in `backend/`: `answer_types.py`, `app_factory.py` (the `/chat` API; tests in `backend/tests/`), `agent_common.py` (agents 2 and 3 only), `config.py`, `speech.py`.
  Test one implementation with e.g. `.venv/bin/python -m pytest backend/impl3_agent_mcp`.
- `rag/` early, experimental offline data preparation. It is only needed by implementation 1 (Chroma); implementations 2 and 3 do not use chunks.
  - `chunk_basic_pages.py`, `chunk_curated_tutorials.py` (shared logic in `chunk_documents.py`): chunk previews for human review
  - `embed_chunks.py`: validate reviewed chunks and write embeddings to local files
  - `export_azure_search_index.py`, `evaluate_azure_search_retrieval.py`: Azure AI Search only, kept from the earlier approach and not used by the current implementations
- `mcp_tools/mcp-server/` Azure Functions MCP server for approved NAIS documents (deployed to a Functions App)
- `widget/` Vite + React + TS widget, built as one IIFE `widget.js`
- `content/` NAIS website sources, reviewed chunks, and local embeddings (local only, not in git; see `.gitignore`)
- `evaluation/` evaluation questions (`questions.json`), the `evaluate_answers.py` runner (all three implementations), saved results in `results/`, and the local Chroma practice database in `chroma/`
- `reference/rag/` notes on RAG technique; the app does not load this folder
- `.env.example` documented environment variables

## Validate and embed reviewed chunks

`rag/embed_chunks.py` (this script does not upload anything) reads `content/chunks/basic-pages/chunks.jsonl` by default; it does not
re-chunk source pages. Local validation makes no model call, and an explicit
`--embed` flag is required to call Foundry and write local embedding files.

## Local full-document agent experiment

To compare retrieval against reading the original Markdown files directly, run:

```bash
.venv/bin/python -m mcp_tools.local_agent.agent "Can I drop in without booking?"
```

This separate experiment searches every approved Markdown file in
`LOCAL_DOCS_DIR` (default: `content/nais-website/clean/zip`), recommends
relevant files, and calls `read_document` to load their complete original text
before answering. It uses no chunks, embeddings, or Azure AI Search index.
Set `LOCAL_AGENT_MODEL` and `LOCAL_AGENT_MAX_ROUNDS` in `.env` as needed. Search
and full-file reads are visible in the CLI log; source filenames appear in the
answer. Answer quality still depends on the selected chat model and the source
documents.

## Environment variables
See [.env.example](.env.example). Azure variables use **deployment names** (not model names), so models stay configurable. API keys live only on the backend.
