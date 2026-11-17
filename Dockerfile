# Stage 1: build the widget bundle (widget/dist/widget.js).
# VITE_API_URL is intentionally unset: the widget then calls the origin it was
# loaded from, so one image works on any host.
FROM node:20-slim AS widget
WORKDIR /widget
COPY widget/package.json widget/package-lock.json ./
RUN npm ci
COPY widget/ ./
RUN npm run build

# Stage 2: FastAPI backend serving /chat, /health and /widget.js.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY deploy/chroma/ data/chroma/
COPY --from=widget /widget/dist widget/dist

# Read-only vector store baked into the image (see deploy/README.md).
ENV CHROMA_PATH=data/chroma
EXPOSE 8000
# Render injects $PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
