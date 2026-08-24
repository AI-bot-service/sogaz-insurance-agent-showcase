"""Нормализация адреса через подсказки DaData.

Клиент диктует адрес как помнит («Алушта Ленина 10», «ул. Ленина д.10 Алушта»),
а поле адреса в AdInsure — справочник ФИАС: пока строка не выбрана из подсказки
портала, значение остаётся сырым текстом, и «Стандартизация» его не разбирает —
адрес в договоре оказывается пустым. Поэтому адрес приводится к каноническому
виду ФИАС ещё в боте: клиент подтверждает его в сводке, а агент вводит в портал
ровно ту строку, которую портал сам показывает в подсказке.

Используются подсказки (`suggestions.dadata.ru`), а не платная стандартизация:
каноническая строка и уровень точности есть и здесь, 10 000 запросов в сутки
бесплатны. Без ключа (`DADATA_API_KEY`) нормализация выключена — бот работает
как раньше, значение уходит в заявку как есть.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger("address")

# Уровень детализации ФИАС: 8 — дом, 9 — квартира. Всё, что меньше, — адрес
# без дома, договор по такому не оформить.
FIAS_LEVEL_HOUSE = 8


@dataclass(frozen=True, slots=True)
class Address:
    """Разобранный адрес: каноническая строка и то, насколько она точна."""

    value: str
    fias_id: str | None = None
    fias_level: int | None = None
    postal_code: str | None = None
    house: str | None = None

    @property
    def has_house(self) -> bool:
        """Есть ли номер дома — без него договор не оформить."""
        if self.house:
            return True
        return self.fias_level is not None and self.fias_level >= FIAS_LEVEL_HOUSE


# Кэш на процесс: в одном диалоге адрес переспрашивают и переписывают по
# нескольку раз, а суточный лимит подсказок общий на всех клиентов.
_CACHE: dict[str, Address | None] = {}
_CACHE_LIMIT = 512


def enabled() -> bool:
    return bool(settings.dadata_api_key)


def _parse(payload: dict) -> Address | None:
    suggestions = payload.get("suggestions") or []
    if not suggestions:
        return None
    first = suggestions[0]
    data = first.get("data") or {}
    raw_level = data.get("fias_level")
    try:
        level = int(raw_level) if raw_level not in (None, "") else None
    except (TypeError, ValueError):
        level = None
    return Address(
        value=(first.get("value") or "").strip(),
        fias_id=data.get("fias_id"),
        fias_level=level,
        postal_code=data.get("postal_code"),
        house=data.get("house"),
    )


async def normalize(query: str) -> Address | None:
    """Адрес клиента → каноническая строка ФИАС.

    `None` — ключа нет, сервис не ответил или адрес не распознан. Вызывающий в
    этом случае оставляет то, что сказал клиент: неотправленная заявка хуже
    заявки с неидеальным адресом.
    """
    text = (query or "").strip()
    if not text or not enabled():
        return None
    if text in _CACHE:
        return _CACHE[text]

    try:
        async with httpx.AsyncClient(timeout=settings.dadata_timeout) as client:
            resp = await client.post(
                settings.dadata_suggest_url,
                headers={
                    "Authorization": f"Token {settings.dadata_api_key}",
                    "Accept": "application/json",
                },
                json={"query": text[:300], "count": 1},
            )
            resp.raise_for_status()
            result = _parse(resp.json())
    except httpx.HTTPError as e:
        # Сеть или лимит запросов: диалог из-за этого не останавливаем.
        logger.warning("DaData: адрес %r не нормализован (%s)", text[:80], e)
        return None
    except ValueError as e:
        logger.warning("DaData: не разобрать ответ (%s)", e)
        return None

    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[text] = result
    if result:
        logger.info("DaData: %r → %r (уровень %s)", text[:60], result.value, result.fias_level)
    return result
