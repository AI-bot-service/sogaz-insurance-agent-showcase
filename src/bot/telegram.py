"""Канал Telegram (aiogram 3): приём текста/голоса/фото → общий сценарий диалога.

Сам сценарий лежит в `app.bot.dialog` и одинаков для Telegram и MAX: здесь
только приём сообщений, скачивание вложений и отправка ответов.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message as TgMessage,
)

from app.bot import dialog
from app.bot.dialog import ACTION_CONFIRM, ACTION_EDIT, Incoming, Reply
from app.digitize import stt

logger = logging.getLogger("bot.telegram")

CHANNEL = "telegram"

# Предел длины сообщения в Telegram — 4096 символов; сводка бывает длиннее.
TEXT_LIMIT = 4096


def _review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Всё верно, рассчитать", callback_data=ACTION_CONFIRM)],
            [InlineKeyboardButton(text="✏️ Исправить", callback_data=ACTION_EDIT)],
        ]
    )


async def _send(msg: TgMessage, replies: list[Reply]) -> None:
    for reply in replies:
        parts = dialog.split_text(reply.text, TEXT_LIMIT)
        for idx, part in enumerate(parts):
            # Кнопки — только под последней частью сводки.
            last = idx == len(parts) - 1
            await msg.answer(
                part, reply_markup=_review_kb() if reply.review_keyboard and last else None
            )


def build_dispatcher(bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(msg: TgMessage) -> None:
        await _send(msg, dialog.start())

    @dp.message(F.voice | F.audio)
    async def on_voice(msg: TgMessage) -> None:
        await msg.chat.do("typing")
        file = await bot.get_file((msg.voice or msg.audio).file_id)
        buf = await bot.download_file(file.file_path)
        text = await stt.transcribe(buf.read())
        if not text:
            await msg.answer("Не расслышал, повторите, пожалуйста.")
            return
        replies = await dialog.incoming(
            CHANNEL, Incoming(user_id=str(msg.from_user.id), text=text, input_type="voice")
        )
        await _send(msg, replies)

    @dp.message(F.photo)
    async def on_photo(msg: TgMessage) -> None:
        await msg.chat.do("typing")
        file = await bot.get_file(msg.photo[-1].file_id)
        buf = await bot.download_file(file.file_path)
        replies = await dialog.incoming(
            CHANNEL,
            Incoming(
                user_id=str(msg.from_user.id),
                text=msg.caption or "",
                images=[(buf.read(), "image/jpeg")],
                input_type="photo",
            ),
        )
        await _send(msg, replies)

    @dp.message(F.text)
    async def on_text(msg: TgMessage) -> None:
        await msg.chat.do("typing")
        replies = await dialog.incoming(
            CHANNEL, Incoming(user_id=str(msg.from_user.id), text=msg.text)
        )
        await _send(msg, replies)

    @dp.callback_query(F.data == ACTION_CONFIRM)
    async def on_confirm(cb: CallbackQuery) -> None:
        replies = await dialog.confirm(CHANNEL, str(cb.from_user.id))
        await _send(cb.message, replies)
        await cb.answer("Готово")

    @dp.callback_query(F.data == ACTION_EDIT)
    async def on_edit(cb: CallbackQuery) -> None:
        replies = await dialog.edit(CHANNEL, str(cb.from_user.id))
        await _send(cb.message, replies)
        await cb.answer()

    return dp
