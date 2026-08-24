"""Распознавание речи (голосовые сообщения) через OpenAI Whisper."""
from __future__ import annotations

import io

from openai import AsyncOpenAI

from app.core.config import settings

_client: AsyncOpenAI | None = None


def _oai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Голос → текст. Telegram шлёт ogg/opus — Whisper понимает."""
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    resp = await _oai().audio.transcriptions.create(
        model=settings.stt_model,
        file=buf,
        language="ru",
    )
    return (resp.text or "").strip()
