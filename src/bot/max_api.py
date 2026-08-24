"""Клиент Bot API мессенджера MAX (https://platform-api2.max.ru).

Готового SDK для Python у MAX нет (официальные библиотеки — JS, Go, Java, PHP),
а API маленькое: получить события, отправить сообщение, ответить на нажатие
кнопки, скачать вложение. Поэтому здесь свой клиент на httpx, который уже есть
в зависимостях, а не ещё одна сторонняя библиотека в контуре с ПДн.

Ответы разбираются в модели pydantic: приходят они из внешней системы, и
молчаливое `dict.get()` по цепочке даёт None там, где ждали id пользователя.
Неизвестные поля игнорируются — MAX расширяет схему, ломаться на этом нельзя.

Схема: https://github.com/max-messenger-bot/max-bot-api-schemas (OpenAPI 0.0.33).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path
from typing import Any, Literal, Self

import certifi
import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("bot.max")

# Сертификат platform-api2.max.ru выпущен «Russian Trusted Sub CA» (НУЦ
# Минцифры) — этого корня нет ни в certifi, ни в системном хранилище образа,
# и запросы падают с CERTIFICATE_VERIFY_FAILED. Корень лежит в репозитории
# (backend/certs) и добавляется ТОЛЬКО в контекст клиента MAX: остальные вызовы
# — OpenAI, Telegram, DaData — проверяются обычным хранилищем.
RU_ROOT_CA = Path(__file__).resolve().parents[2] / "certs" / "russian_trusted_root_ca.crt"

# Предел длины текста сообщения (NewMessageBody.text, maxLength 4000).
TEXT_LIMIT = 4000

# Типы событий, которые нужны боту. Остальные (правка/удаление сообщений,
# добавление в чат) не запрашиваем, чтобы не разбирать лишнее.
UPDATE_TYPES = ("message_created", "message_callback", "bot_started")


class MaxApiError(RuntimeError):
    """Ошибка вызова Bot API MAX."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"MAX API {status}: {code} — {message}")
        self.status = status
        self.code = code


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MaxUser(_Model):
    user_id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False
    name: str = ""


class MaxRecipient(_Model):
    chat_id: int | None = None
    chat_type: str | None = None
    user_id: int | None = None


class MaxAttachmentPayload(_Model):
    """Полезная нагрузка вложения: ссылка на файл и его токен.

    `url` у изображений и медиа живёт ограниченное время — скачивать сразу,
    а не откладывать.
    """

    url: str | None = None
    token: str | None = None
    photo_id: int | None = None


class MaxAttachment(_Model):
    type: str
    payload: MaxAttachmentPayload | None = None
    filename: str | None = None
    size: int | None = None
    # У голосовых сообщений MAX иногда сам присылает расшифровку — запасной
    # путь, если файл не удалось скачать.
    transcription: str | None = None


class MaxMessageBody(_Model):
    mid: str = ""
    seq: int | None = None
    text: str | None = None
    attachments: list[MaxAttachment] = Field(default_factory=list)


class MaxMessage(_Model):
    sender: MaxUser | None = None
    recipient: MaxRecipient | None = None
    timestamp: int | None = None
    body: MaxMessageBody = Field(default_factory=MaxMessageBody)


class MaxCallback(_Model):
    callback_id: str
    payload: str | None = None
    user: MaxUser | None = None
    timestamp: int | None = None


class MaxUpdate(_Model):
    """Событие из /updates. Набор полей зависит от `update_type`."""

    update_type: str
    timestamp: int | None = None
    message: MaxMessage | None = None
    callback: MaxCallback | None = None
    # bot_started
    chat_id: int | None = None
    user: MaxUser | None = None
    user_locale: str | None = None


class MaxUpdateList(_Model):
    updates: list[MaxUpdate] = Field(default_factory=list)
    marker: int | None = None


def ssl_context(extra_ca: Path = RU_ROOT_CA) -> ssl.SSLContext:
    """Проверка сертификатов: обычное хранилище плюс корень Минцифры.

    Файла нет — работаем на одном certifi: пусть лучше запрос упадёт на
    проверке сертификата, чем бот пойдёт в сеть без неё.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    if extra_ca.is_file():
        ctx.load_verify_locations(cafile=str(extra_ca))
    else:
        logger.warning("MAX: корневой сертификат %s не найден", extra_ca)
    return ctx


class MaxBot:
    """Тонкая обёртка над Bot API MAX.

    Транспорт получения событий здесь не зашит: `get_updates` — для long
    polling, а тот же разбор `MaxUpdate` подойдёт и вебхуку, когда бота
    переведут на `POST /subscriptions`.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://platform-api2.max.ru",
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ValueError("токен MAX-бота пуст")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            # Токен идёт заголовком: параметр запроса MAX больше не принимает,
            # да и в логах прокси он бы осел целиком.
            headers={"Authorization": token},
            timeout=timeout,
            verify=ssl_context(),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _request(
        self,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = await self._client.request(
            method, path, params=clean, json=json, timeout=timeout
        )
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except ValueError:
                err = {}
            raise MaxApiError(
                resp.status_code,
                str(err.get("code", "unknown")),
                str(err.get("message", resp.text[:200])),
            )
        return resp.json() if resp.content else {}

    async def get_me(self) -> MaxUser:
        return MaxUser.model_validate(await self._request("GET", "/me"))

    async def get_updates(
        self, *, marker: int | None = None, timeout: int = 30, limit: int = 100
    ) -> MaxUpdateList:
        """Забрать события. Держит соединение до `timeout` секунд."""
        data = await self._request(
            "GET",
            "/updates",
            params={
                "marker": marker,
                "timeout": timeout,
                "limit": limit,
                "types": ",".join(UPDATE_TYPES),
            },
            # Ждём дольше самого long polling: иначе httpx рвёт соединение
            # ровно тогда, когда сервер ещё вправе молчать.
            timeout=timeout + 15,
        )
        return MaxUpdateList.model_validate(data)

    async def send_message(
        self,
        text: str,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> None:
        """Отправить сообщение в диалог. Длинный текст режется на части.

        Клавиатура прикладывается к последней части: кнопки должны стоять под
        концом сводки, а не в середине.
        """
        from app.bot.dialog import split_text  # локально: dialog импортирует канал

        parts = split_text(text, TEXT_LIMIT)
        for idx, part in enumerate(parts):
            body: dict[str, Any] = {"text": part}
            if buttons and idx == len(parts) - 1:
                body["attachments"] = [
                    {"type": "inline_keyboard", "payload": {"buttons": buttons}}
                ]
            await self._request(
                "POST",
                "/messages",
                params={"user_id": user_id, "chat_id": chat_id},
                json=body,
            )

    async def answer_callback(self, callback_id: str) -> None:
        """Снять «часики» с нажатой кнопки. Текст отправляется отдельно."""
        await self._request("POST", "/answers", params={"callback_id": callback_id}, json={})

    async def send_action(self, chat_id: int, action: str = "typing_on") -> None:
        """Показать «бот печатает». Ошибку глушим: это украшение, не логика."""
        try:
            await self._request("POST", f"/chats/{chat_id}/actions", json={"action": action})
        except (MaxApiError, httpx.HTTPError) as e:
            logger.debug("MAX: не удалось отправить действие %s: %s", action, e)

    async def download(self, url: str) -> bytes:
        """Скачать вложение по ссылке из события."""
        resp = await self._client.get(url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.content


async def poll(
    bot: MaxBot,
    handler: Any,
    *,
    poll_timeout: int = 30,
    stop: asyncio.Event | None = None,
) -> None:
    """Цикл long polling: события отдаются `handler(bot, update)`.

    Сбой сети не должен ронять бота: ошибку логируем и ждём, а `marker`
    двигаем только после успешного запроса — иначе события потерялись бы.
    """
    marker: int | None = None
    backoff = 1.0
    while not (stop and stop.is_set()):
        try:
            batch = await bot.get_updates(marker=marker, timeout=poll_timeout)
            backoff = 1.0
        except (MaxApiError, httpx.HTTPError) as e:
            logger.warning("MAX: опрос обновлений не удался (%s), повтор через %.0f с", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue

        for update in batch.updates:
            try:
                await handler(bot, update)
            except Exception:  # один сбойный ход не рвёт цикл
                logger.exception("MAX: ошибка обработки события %s", update.update_type)

        if batch.marker is not None:
            marker = batch.marker
