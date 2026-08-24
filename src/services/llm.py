"""LLM-клиент (OpenAI-совместимый: OpenRouter или OpenAI).

Единая точка обращения к чат-модели: диалог-движок, оцифровка, верификация,
распознавание фото (vision). Модель по умолчанию — google/gemini-2.0-flash-001
через OpenRouter (config.llm_model): дёшево, есть vision и tool-calling.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if settings.llm_provider == "openai":
            _client = AsyncOpenAI(api_key=settings.openai_api_key)
        else:
            _client = AsyncOpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base,
            )
    return _client


async def chat(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    response_json: bool = False,
) -> Any:
    """Один вызов chat.completions. Возвращает объект message."""
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, *messages],
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client().chat.completions.create(**kwargs)
    return resp.choices[0].message


# ---- хелперы формирования сообщений -----------------------------------

def user_text(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def user_image(text: str, image_bytes: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    """Сообщение с картинкой (vision) в OpenAI-формате."""
    b64 = base64.b64encode(image_bytes).decode()
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }


def tool_def(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


# ---- разбор ответа ----------------------------------------------------

def text_of(message: Any) -> str:
    return (message.content or "").strip()


def tool_calls(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Вернуть [(имя_функции, аргументы_dict), ...]."""
    out: list[tuple[str, dict[str, Any]]] = []
    for tc in message.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        out.append((tc.function.name, args))
    return out
