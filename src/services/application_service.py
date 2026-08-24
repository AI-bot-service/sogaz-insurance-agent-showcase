"""Сервис работы с заявками: поиск/создание, история диалога, сохранение."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application, ApplicationStatus
from app.models.conversation import Conversation, Message, MessageRole

ACTIVE_STATUSES = (
    ApplicationStatus.collecting,
    ApplicationStatus.verifying,
    ApplicationStatus.ready,
)


async def get_active(
    session: AsyncSession, channel: str, external_user_id: str
) -> Application | None:
    stmt = (
        select(Application)
        .where(
            Application.channel == channel,
            Application.external_user_id == external_user_id,
            Application.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Application.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_or_create(
    session: AsyncSession, channel: str, external_user_id: str
) -> Application:
    app = await get_active(session, channel, external_user_id)
    if app:
        return app
    app = Application(
        object_type="",
        channel=channel,
        external_user_id=external_user_id,
        values={},
        groups={},
    )
    session.add(app)
    await session.flush()
    return app


async def _conversation(
    session: AsyncSession, app: Application
) -> Conversation:
    stmt = (
        select(Conversation)
        .where(Conversation.application_id == app.id)
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conv = (await session.execute(stmt)).scalar_one_or_none()
    if conv is None:
        conv = Conversation(
            application_id=app.id, channel=app.channel, external_user_id=app.external_user_id
        )
        session.add(conv)
        await session.flush()
    return conv


async def load_history(
    session: AsyncSession, app: Application, limit: int = 12
) -> list[dict[str, Any]]:
    """Последние сообщения в формате OpenAI chat (user/assistant)."""
    conv = await _conversation(session, app)
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conv.id,
            Message.role.in_([MessageRole.user, MessageRole.assistant]),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))
    return [{"role": m.role.value, "content": m.content} for m in rows]


async def log_message(
    session: AsyncSession,
    app: Application,
    role: MessageRole,
    content: str,
    input_type: str = "text",
    meta: dict | None = None,
) -> None:
    conv = await _conversation(session, app)
    session.add(
        Message(
            conversation_id=conv.id,
            role=role,
            content=content,
            input_type=input_type,
            meta=meta,
        )
    )


async def by_id(session: AsyncSession, app_id: uuid.UUID) -> Application | None:
    return await session.get(Application, app_id)
