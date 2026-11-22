# Project: NAIS Chat Assistant (MVP)

## Goal
Build an MVP AI chat assistant for Nelson AI Sandbox (NAIS). It is ONLY a floating chat window in the bottom-right corner, not a whole website. The NAIS site runs on WordPress and will embed it with a single <script> tag. The assistant answers visitor questions about NAIS using RAG over NAIS-provided documents, and must not invent facts.

## Tech stack
- Widget: React + TypeScript, built with Vite in library mode as a single self-contained IIFE bundle (widget.js). Mounted inside a Shadow DOM so WordPress theme CSS cannot clash with it.
- Backend: Python 3.11+, FastAPI, uvicorn.
- RAG: Azure AI Search (hybrid keyword + vector retrieval) + Microsoft Foundry model deployments for embeddings and generation. Use the `openai` SDK with the Foundry OpenAI-compatible inference base URL. This supersedes the earlier ChromaDB and separate Azure OpenAI resource assumptions. Deployment names and the search index must be configurable via environment variables, not hardcoded.
- Config: python-dotenv. API keys live ONLY on the backend.

## Repo structure (monorepo: nais-chat-assistant)
nais-chat-assistant/
├── backend/
│   ├── main.py          # FastAPI app, /chat and /health endpoints, CORS, rate limiting
│   ├── answer_chat_completions.py # (Chroma + chat completions) # query-time: embed the question, retrieve chunks, write the answer
│   ├── config.py        # env-based settings
│   └── requirements.txt
├── rag/
│   ├── chunk_documents.py          # shared chunk preview helpers
│   ├── chunk_basic_pages.py        # preview chunks for basic pages
│   ├── chunk_curated_tutorials.py  # preview chunks for curated tutorials
│   ├── embed_chunks.py             # validate reviewed JSONL and embed it into local files
│   ├── export_azure_search_index.py             # back up the Azure AI Search index
│   └── evaluate_azure_search_retrieval.py       # batch-check retrieval against evaluation/questions.json
├── widget/
│   ├── src/
│   │   ├── main.tsx         # creates host div, attaches Shadow DOM, mounts React
│   │   ├── ChatWidget.tsx   # bubble button + chat window
│   │   ├── api.ts           # calls POST /chat
│   │   └── styles.css       # injected into the shadow root
│   ├── test.html            # bare page for local testing
│   ├── vite.config.ts
│   └── package.json
├── content/             # NAIS website sources, reviewed chunks, embeddings; no secrets
├── evaluation/          # Retrieval questions, results, and the local Chroma practice database
├── reference/rag/       # RAG technique notes; not loaded by the app
├── .env.example
├── .gitignore
└── README.md

## Backend requirements
1. POST /chat
   - Request: { "message": string, "history": [{ "role": "user" | "assistant", "content": string }] }
   - Response: { "answer": string, "sources": [{ "title": string, "snippet": string, "url"?: string }] }
   - Validate input with Pydantic: max message length 1000 chars, history capped at the last 6 turns.
2. GET /health returns { "status": "ok" }.
3. RAG pipeline:
   - The chunking scripts produce reviewed JSONL in content/chunks/. The ingest script reads approved chunks, embeds them, and uploads them to Azure AI Search with source metadata (filename/title/url).
   - At query time: embed the question with the same Foundry embedding deployment, retrieve top 4 chunks, build a prompt, and call the Foundry chat deployment.
   - System prompt: answer ONLY from the provided context; be concise and friendly; if the context does not contain the answer, say you don't know and suggest contacting NAIS (contact details configurable via env, NAIS_CONTACT_INFO); never fabricate details about NAIS.
   - If the best retrieval score is below a configurable threshold, skip the LLM and return the fallback message.
4. Security and cost control:
   - CORS restricted to origins in ALLOWED_ORIGINS (env, comma-separated); localhost allowed in dev.
   - Per-IP rate limiting (e.g. slowapi), configurable.
   - Never log API keys or full user messages beyond what is needed for debugging.
   - Handle Foundry model errors and timeouts gracefully and return a friendly error message.
5. Serve the built widget.js as a static file from the backend (/widget.js) so NAIS only needs: <script src="https://<api-host>/widget.js" defer></script>

## Widget requirements
1. Floating circular button bottom-right (fixed position, high z-index). Click toggles a chat window (~360x500px desktop; full-width, near-full-height on mobile).
2. Header with title "NAIS Assistant" and a close button. Message list with user/assistant bubbles, auto-scroll, a typing indicator while waiting, and an input with Enter-to-send.
3. Welcome message plus 2-3 suggested starter questions.
4. Show source links/snippets under assistant answers when available.
5. Error state with a retry option when the API fails.
6. Accessible: aria-labels, keyboard focus handling, Escape closes the window, sufficient colour contrast.
7. API base URL configurable at build time (VITE_API_URL) and overridable via a data attribute on the script tag.
8. All CSS is scoped inside the Shadow DOM. Do not use global styles or depend on any host-page library. React is bundled in (no external globals).
9. Keep the bundle small. Do not add heavy UI libraries.

## Coding standards
- TypeScript strict mode. Python type hints. Small, well-named functions and clear comments where logic is non-obvious.
- No hardcoded secrets or URLs. Everything environment-driven, documented in .env.example.
- Python: ruff/black formatting. TS: ESLint + Prettier.
- Do not add dependencies without a clear reason, and keep requirements.txt and package.json minimal.

## .gitignore must include
.env, node_modules/, widget/dist/, content/chunks/, __pycache__/, .venv/

## Build order (do one step at a time and stop for my review after each)
1. Scaffold the folder structure, config files, .env.example, .gitignore and a README skeleton.
2. Backend config, `rag/ingest.py`, and `backend/answer_chat_completions.py`. Upload reviewed chunks to Azure AI Search and verify retrieval from the CLI.
3. Backend: FastAPI /chat and /health with CORS and rate limiting. Test with curl.
4. Widget: Vite + React + TS project, Shadow DOM mounting, ChatWidget UI, wired to /chat. Test via widget/test.html.
5. Production build as a single IIFE widget.js, served by the backend.
6. README: setup, ingest steps, running locally, embedding on WordPress (e.g. via the WPCode plugin or the theme footer), deployment notes (Render/Railway/Azure), env variable reference.

## Out of scope for the MVP
Authentication, user accounts, a database for chat history, an admin dashboard, multi-language support, streaming responses (nice-to-have later).

## Acceptance criteria
- Pasting the script tag into widget/test.html shows the chat bubble bottom-right, and a question about approved content in the Azure AI Search index returns a correct, grounded answer with a source.
- A question outside the data returns the "I don't know / contact NAIS" fallback, not a guess.
- The API key is never exposed in the browser bundle or network calls.
- Requests from disallowed origins are rejected by CORS.

Before writing code, briefly confirm your plan for step 1 and ask me about anything ambiguous.
