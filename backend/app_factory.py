"""FastAPI app shared by every implementation: /chat, /health, /tts, /stt, widget.

``create_app(answer_question)`` builds the app around one answer function, so
each implementation gets its own entry module (``main_*.py``) and nothing in this
file knows how an answer is produced.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.answer_types import Answer, AnswerError
from backend.config import ROOT_DIR, settings
from backend.speech import SpeechError, synthesize, transcribe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nais.api")

MAX_HISTORY_TURNS = 6
MAX_AUDIO_BYTES = 2_500_000  # ~75 s of 16 kHz 16-bit mono; Azure's short-audio limit is 60 s
WIDGET_BUNDLE = ROOT_DIR / "widget/dist/widget.js"
DEMO_PAGE = ROOT_DIR / "widget/test.html"
LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

AnswerFunction = Callable[[str, list[dict[str, str]]], Answer]
# Yields {"type": "delta", "text"} events, then one {"type": "done", "answer", "sources"}.
StreamFunction = Callable[[str, list[dict[str, str]]], Iterator[dict[str, Any]]]


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


class STTResponse(BaseModel):
    text: str


def create_app(
    answer_question: AnswerFunction,
    stream_answer: StreamFunction | None = None,
) -> FastAPI:
    """Build the API around one implementation's ``answer_question``.

    ``stream_answer`` is optional; when given, ``POST /chat/stream`` is added
    (Server-Sent Events). Without it that route does not exist (404).
    """
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

    @app.post("/tts", include_in_schema=True)
    @limiter.limit(settings.tts_rate_limit)
    def tts(request: Request, body: TTSRequest) -> Response:
        """Read ``text`` aloud; returns mp3 audio."""
        try:
            audio = synthesize(body.text.strip())
        except SpeechError as exc:
            logger.error("Speech failure: %s", exc)
            raise HTTPException(
                status_code=503, detail="Voice is unavailable right now."
            ) from exc
        return Response(content=audio, media_type="audio/mpeg")

    @app.post("/stt", response_model=STTResponse)
    @limiter.limit(settings.stt_rate_limit)
    async def stt(request: Request) -> STTResponse:
        """Transcribe a short 16 kHz mono WAV clip sent as the raw request body."""
        audio = await request.body()
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio is empty or too long.")
        try:
            text = await run_in_threadpool(transcribe, audio)
        except SpeechError as exc:
            logger.error("Transcription failure: %s", exc)
            raise HTTPException(
                status_code=503, detail="Voice input is unavailable right now."
            ) from exc
        return STTResponse(text=text)

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

    if stream_answer is not None:

        @app.post("/chat/stream")
        def chat_stream(request: ChatRequest) -> StreamingResponse:
            """Same as /chat, but sent as Server-Sent Events while it is written."""
            history = [turn.model_dump() for turn in request.history]

            def events() -> Iterator[str]:
                try:
                    for event in stream_answer(request.message, history):
                        yield f"data: {json.dumps(event)}\n\n"
                except AnswerError as exc:
                    # Headers are already sent, so report the failure as an event.
                    logger.error("Answer failure: %s", exc)
                    error = {
                        "type": "error",
                        "message": "The assistant is temporarily unavailable. "
                        "Please try again soon.",
                    }
                    yield f"data: {json.dumps(error)}\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                # No caching, and ask proxies (nginx) not to buffer the stream.
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    return app
