# Deploying to Render (test environment)

`deploy/chroma/` is a copy of the vector store (collection `nais-chroma-practice`),
committed so the Docker image carries it. Refresh it after re-ingesting:

    rm -rf deploy/chroma && cp -R evaluation/chroma/db deploy/chroma

## Steps

1. Commit and push to GitHub (`git add -A && git commit && git push`).
2. Render dashboard > New > Blueprint > pick this repo (reads `render.yaml`).
3. Fill in the secret env vars when prompted (same values as your local `.env`):
   `FOUNDRY_OPENAI_BASE_URL`, `FOUNDRY_API_KEY`, `FOUNDRY_CHAT_DEPLOYMENT`,
   `FOUNDRY_EMBEDDING_DEPLOYMENT`, optionally `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION`.
4. `ALLOWED_ORIGINS`: the origins of pages that will embed the widget, comma-separated,
   e.g. `https://nais-chat-assistant.onrender.com,https://your-test-site.example`.
5. After deploy, check `https://<service>.onrender.com/health` returns `{"status":"ok"}`.

## Embedding for testers

    <script src="https://<service>.onrender.com/widget.js" defer></script>

The widget calls the origin it was loaded from, so no `VITE_API_URL` is needed.
Pages on other origins must be listed in `ALLOWED_ORIGINS`.
