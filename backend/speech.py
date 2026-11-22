"""Text-to-speech via the Azure Speech REST API (key stays on the backend)."""

from __future__ import annotations

import logging
from functools import lru_cache
from xml.sax.saxutils import escape

import httpx

from backend.config import settings

logger = logging.getLogger("nais.speech")

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


class SpeechError(Exception):
    """Speech synthesis failed or is not configured."""


def _endpoint() -> str:
    if settings.azure_speech_endpoint:
        return settings.azure_speech_endpoint
    return (
        f"https://{settings.azure_speech_region}.tts.speech.microsoft.com"
        "/cognitiveservices/v1"
    )


def _ssml(text: str) -> str:
    voice = settings.azure_speech_voice
    lang = "-".join(voice.split("-")[:2])
    return (
        f"<speak version='1.0' xml:lang='{lang}'>"
        f"<voice name='{voice}'>{escape(text)}</voice></speak>"
    )


@lru_cache(maxsize=32)
def synthesize(text: str) -> bytes:
    """Return mp3 bytes for ``text``. Cached so repeated greetings cost nothing."""
    if not settings.speech_ready():
        raise SpeechError("Speech is not configured")
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "nais-chat-assistant",
    }
    try:
        response = httpx.post(
            _endpoint(),
            headers=headers,
            content=_ssml(text).encode("utf-8"),
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Log the error type only; never the text or the key.
        raise SpeechError(type(exc).__name__) from exc
    return response.content


def _stt_endpoint() -> str:
    if settings.azure_speech_stt_endpoint:
        return settings.azure_speech_stt_endpoint
    return (
        f"https://{settings.azure_speech_region}.stt.speech.microsoft.com"
        "/speech/recognition/conversation/cognitiveservices/v1"
    )


def transcribe(wav: bytes) -> str:
    """Speech-to-text for a short (<60 s) 16 kHz mono PCM WAV clip.

    Returns an empty string when no speech was recognised.
    """
    if not settings.speech_ready() or not (
        settings.azure_speech_region or settings.azure_speech_stt_endpoint
    ):
        raise SpeechError("Speech is not configured")
    try:
        response = httpx.post(
            _stt_endpoint(),
            params={"language": settings.azure_speech_stt_language, "format": "simple"},
            headers={
                "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                "Accept": "application/json",
            },
            content=wav,
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Log the error type only; never the audio or the key.
        raise SpeechError(type(exc).__name__) from exc
    if result.get("RecognitionStatus") != "Success":
        return ""
    return str(result.get("DisplayText", "")).strip()
