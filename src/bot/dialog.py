"""Сценарий диалога, общий для всех каналов (Telegram, MAX).

Мессенджер отвечает только за приём сообщения и отправку ответа: что спросить,
что записать в заявку и когда показать сводку — решается здесь. Иначе каждый
новый канал повторял бы логику, и они расходились бы по мелочам.

Канал вызывает четыре входа: `start`, `incoming`, `confirm`, `edit` — и
отправляет полученные `Reply` пользователю, включая клавиатуру подтверждения
там, где `review_keyboard`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.bot import engine
from app.calc.providers import get_provider
from app.core.db import SessionLocal
from app.models.application import ApplicationStatus
from app.models.conversation import MessageRole
from app.services import application_service as apps
from app.services import verify

logger = logging.getLogger("bot.dialog")

# Коды кнопок под сводкой. Один набор на все каналы: и в Telegram
# (callback_data), и в MAX (payload кнопки) приходят эти же строки.
ACTION_CONFIRM = "confirm"
ACTION_EDIT = "edit"

WELCOME = (
    "Здравствуйте! Я помогу оформить страхование имущества СОГАЗ "
    "«Персональное решение+» — квартиры или дома.\n\n"
    "Расскажите, что хотите застраховать: квартиру или дом? "
    "Можно писать текстом, надиктовать голосом или прислать фото объекта."
)

REVIEW_INTRO = "Проверьте, пожалуйста, собранные данные:\n\n"
EDIT_PROMPT = "Что нужно поправить? Напишите пункт и верное значение."
NO_APPLICATION = "Активной заявки нет. Напишите, что хотите застраховать."
MISSING_INTRO = (
    "Чтобы отправить заявку на расчёт, не хватает данных:\n\n"
)


@dataclass(slots=True)
class Reply:
    """Одно сообщение клиенту. `review_keyboard` — приложить кнопки под сводкой."""

    text: str
    review_keyboard: bool = False


@dataclass(slots=True)
class Incoming:
    """Ход клиента, приведённый к общему виду каналом.

    `images` — [(bytes, mime), ...]: фото уже скачано каналом, движок работает
    с байтами и не знает, откуда они взялись.
    """

    user_id: str
    text: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)
    input_type: str = "text"


def start() -> list[Reply]:
    return [Reply(WELCOME)]


async def incoming(channel: str, msg: Incoming) -> list[Reply]:
    """Обработать ход клиента: записать в заявку и вернуть, что ответить."""
    async with SessionLocal() as session:
        app = await apps.get_or_create(session, channel, msg.user_id)
        await apps.log_message(
            session, app, MessageRole.user, msg.text or "[медиа]", msg.input_type
        )
        history = await apps.load_history(session, app)

        reply = await engine.handle_turn(
            session, app, text=msg.text, images=msg.images or None, history=history
        )
        await apps.log_message(session, app, MessageRole.assistant, reply.text)

        replies = [Reply(reply.text)]
        if reply.ready_for_review:
            replies.append(Reply(REVIEW_INTRO + verify.build_summary(app), review_keyboard=True))

        await session.commit()
        return replies


async def confirm(channel: str, user_id: str) -> list[Reply]:
    """Клиент подтвердил сводку — отправляем заявку на расчёт."""
    async with SessionLocal() as session:
        app = await apps.get_active(session, channel, user_id)
        if not app:
            return [Reply(NO_APPLICATION)]

        # Последняя проверка перед СК: заявка без обязательного (у дома — без
        # единого строения) в форме превращается в пустой договор, и оператор
        # разбирает это уже вручную. Кнопку подтверждения бот показывает
        # только по собранной заявке, но в статусе проверки могли остаться
        # старые — их сюда пускать нельзя.
        missing = verify.missing_report(app)
        if missing:
            app.status = ApplicationStatus.collecting
            await session.commit()
            return [Reply(MISSING_INTRO + "\n".join(f"• {m}" for m in missing))]

        app.status = ApplicationStatus.ready
        result = await get_provider().quote(app)
        app.quote = result.as_dict()
        app.status = (
            ApplicationStatus.quoted
            if result.status == "quoted"
            else ApplicationStatus.calculating
        )
        await session.commit()

    if result.status == "quoted" and result.premium:
        text = f"💰 Стоимость полиса: {int(result.premium):,} ₽".replace(",", " ")
        if result.policy_draft_url:
            text += f"\nПроект полиса: {result.policy_draft_url}"
        return [Reply(text)]
    return [Reply(result.message or "Заявка принята в работу.")]


async def edit(channel: str, user_id: str) -> list[Reply]:
    """Клиент хочет исправить данные — возвращаем заявку в сбор."""
    async with SessionLocal() as session:
        app = await apps.get_active(session, channel, user_id)
        if app:
            app.status = ApplicationStatus.collecting
            await session.commit()
    return [Reply(EDIT_PROMPT)]


def split_text(text: str, limit: int) -> list[str]:
    """Нарезать длинный ответ на части, которые примет мессенджер.

    Сводка по дому с пятью строениями уходит за 4000 символов, и сообщение
    целиком отвергается сервером — клиент не видит ничего. Режем по строкам,
    чтобы пункт сводки не разрывался посередине.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    chunk = ""
    for line in text.split("\n"):
        # Строка длиннее лимита сама по себе — режем её по границе лимита.
        while len(line) > limit:
            if chunk:
                parts.append(chunk)
                chunk = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(chunk) + len(line) + 1 > limit:
            parts.append(chunk)
            chunk = line
        else:
            chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        parts.append(chunk)
    return parts
