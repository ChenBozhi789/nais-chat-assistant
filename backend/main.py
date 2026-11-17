"""Minimal FastAPI app: GET /health, POST /chat and the widget bundle.

Run from the repo root: ``uvicorn backend.main:app --reload``
(Rate limiting comes in a later step.)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.answer import AnswerError, answer_question
from backend.config import ROOT_DIR, settings
from backend.speech import SpeechError, synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nais.api")

MAX_HISTORY_TURNS = 6
WIDGET_BUNDLE = ROOT_DIR / "widget/dist/widget.js"
DEMO_PAGE = ROOT_DIR / "widget/test.html"
LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

app = FastAPI(title="NAIS Chat Assistant")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Browsers only let pages on these origins call /chat. (The <script> tag that
# loads /widget.js is not subject to CORS.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=LOCALHOST_ORIGIN_REGEX if settings.allow_localhost else None,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def log_rejected_origin(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Say which origin was refused; the CORS middleware only returns a bare 400."""
    response = await call_next(request)
    if request.method == "OPTIONS" and response.status_code == 400:
        logger.warning(
            "CORS preflight rejected for origin %r. Serve the page over "
            "http://localhost, or add the origin to ALLOWED_ORIGINS.",
            request.headers.get("origin"),
        )
    return response


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @field_validator("history")
    @classmethod
    def keep_last_turns(cls, value: list[Turn]) -> list[Turn]:
        return value[-MAX_HISTORY_TURNS:]


class Source(BaseModel):
    title: str
    snippet: str
    url: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


@app.post("/tts", include_in_schema=True)
@limiter.limit(settings.tts_rate_limit)
def tts(request: Request, body: TTSRequest) -> Response:
    """Read ``text`` aloud; returns mp3 audio."""
    try:
        audio = synthesize(body.text.strip())
    except SpeechError as exc:
        logger.error("Speech failure: %s", exc)
        raise HTTPException(status_code=503, detail="Voice is unavailable right now.") from exc
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def demo_page() -> Response:
    """Serve the mock host page so testers can try the widget at the root URL."""
    if not DEMO_PAGE.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    # The page hardcodes localhost for local dev; point it at this server instead.
    html = DEMO_PAGE.read_text(encoding="utf-8").replace(
        "http://localhost:8000/widget.js", "/widget.js"
    )
    return Response(html, media_type="text/html")


@app.get("/widget.js", include_in_schema=False)
def widget_bundle() -> FileResponse:
    """Serve the built widget so NAIS only needs one <script> tag."""
    if not WIDGET_BUNDLE.is_file():
        raise HTTPException(
            status_code=404,
            detail="widget.js not built yet: run `npm run build` in widget/",
        )
    return FileResponse(
        WIDGET_BUNDLE,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    history = [turn.model_dump() for turn in request.history]
    try:
        result = answer_question(request.message, history)
    except AnswerError as exc:
        # Log the cause, never the user's message; return a friendly message.
        logger.error("Answer failure: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="The assistant is temporarily unavailable. Please try again soon.",
        ) from exc
    return ChatResponse(answer=result.text, sources=result.sources)
