"""Канал MAX: события Bot API → общий сценарий диалога.

Разбор события отделён от способа его получения: сейчас события приходят long
polling'ом (`max_api.poll`), при переезде на вебхук тот же `handle_update`
вызовется из обработчика `POST /subscriptions`-колбэка — менять придётся только
точку входа.
"""
from __future__ import annotations

import logging

import httpx

from app.bot import dialog
from app.bot.dialog import ACTION_CONFIRM, ACTION_EDIT, Incoming, Reply
from app.bot.max_api import MaxApiError, MaxAttachment, MaxBot, MaxUpdate
from app.digitize import stt

logger = logging.getLogger("bot.max")

CHANNEL = "max"

# Кнопки под сводкой. Текст короткий: в MAX при двух кнопках в ряду
# рекомендованный предел — 10 символов, поэтому ставим их в два ряда.
REVIEW_BUTTONS = [
    [{"type": "callback", "text": "✅ Всё верно, рассчитать", "payload": ACTION_CONFIRM}],
    [{"type": "callback", "text": "✏️ Исправить", "payload": ACTION_EDIT}],
]

VOICE_UNCLEAR = "Не расслышал, повторите, пожалуйста."
UNSUPPORTED = (
    "Такой файл я пока не разбираю. Напишите текстом, надиктуйте голосом "
    "или пришлите фото объекта."
)

# Картинку MAX отдаёт без типа содержимого, а vision-модели он нужен верным.
# Определяем по сигнатуре файла.
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _image_mime(data: bytes) -> str:
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def _send(bot: MaxBot, chat_id: int | None, user_id: int, replies: list[Reply]) -> None:
    for reply in replies:
        await bot.send_message(
            reply.text,
            chat_id=chat_id,
            user_id=None if chat_id else user_id,
            buttons=REVIEW_BUTTONS if reply.review_keyboard else None,
        )


async def _download(bot: MaxBot, att: MaxAttachment) -> bytes | None:
    url = att.payload.url if att.payload else None
    if not url:
        logger.warning("MAX: вложение %s пришло без ссылки", att.type)
        return None
    try:
        return await bot.download(url)
    except httpx.HTTPError as e:
        logger.warning("MAX: не удалось скачать вложение %s: %s", att.type, e)
        return None


async def _voice_text(bot: MaxBot, att: MaxAttachment) -> str:
    """Голосовое → текст. Своё распознавание, расшифровка MAX — запасной путь."""
    data = await _download(bot, att)
    if data:
        text = await stt.transcribe(data, filename="voice.m4a")
        if text:
            return text
    return (att.transcription or "").strip()


async def handle_update(bot: MaxBot, update: MaxUpdate) -> None:
    """Обработать одно событие MAX."""
    if update.update_type == "bot_started":
        user = update.user
        if user:
            await _send(bot, update.chat_id, user.user_id, dialog.start())
        return

    if update.update_type == "message_callback":
        await _handle_callback(bot, update)
        return

    if update.update_type == "message_created":
        await _handle_message(bot, update)


async def _handle_callback(bot: MaxBot, update: MaxUpdate) -> None:
    cb = update.callback
    if not cb or not cb.user:
        return
    user_id = cb.user.user_id
    chat_id = update.message.recipient.chat_id if update.message and update.message.recipient else None

    # Часики на кнопке снимаем до расчёта: провайдер может думать секунды.
    try:
        await bot.answer_callback(cb.callback_id)
    except MaxApiError as e:
        logger.warning("MAX: не удалось ответить на нажатие кнопки: %s", e)

    if cb.payload == ACTION_CONFIRM:
        replies = await dialog.confirm(CHANNEL, str(user_id))
    elif cb.payload == ACTION_EDIT:
        replies = await dialog.edit(CHANNEL, str(user_id))
    else:
        logger.info("MAX: неизвестная кнопка %r", cb.payload)
        return
    await _send(bot, chat_id, user_id, replies)


async def _handle_message(bot: MaxBot, update: MaxUpdate) -> None:
    msg = update.message
    if not msg or not msg.sender or msg.sender.is_bot:
        return

    user_id = msg.sender.user_id
    chat_id = msg.recipient.chat_id if msg.recipient else None
    text = (msg.body.text or "").strip()

    if text.startswith("/start"):
        await _send(bot, chat_id, user_id, dialog.start())
        return

    if chat_id:
        await bot.send_action(chat_id)

    images: list[tuple[bytes, str]] = []
    input_type = "text"

    for att in msg.body.attachments:
        if att.type == "image":
            data = await _download(bot, att)
            if data:
                images.append((data, _image_mime(data)))
                input_type = "photo"
        elif att.type == "audio":
            voice = await _voice_text(bot, att)
            if not voice:
                await _send(bot, chat_id, user_id, [Reply(VOICE_UNCLEAR)])
                return
            text = f"{text} {voice}".strip() if text else voice
            input_type = "voice"

    if not text and not images:
        # Стикер, видео, документ — сценарий с ними не работает.
        await _send(bot, chat_id, user_id, [Reply(UNSUPPORTED)])
        return

    replies = await dialog.incoming(
        CHANNEL, Incoming(user_id=str(user_id), text=text, images=images, input_type=input_type)
    )
    await _send(bot, chat_id, user_id, replies)
