"""Задача на ввод заявки в AdInsure силами Windows-агента.

Портал adinsure.sogaz.ru закрыт для доступа с сервера (фильтрация по IP) и
защищён двухфакторной авторизацией, поэтому ввод выполняет агент на машине
страхового агента: он забирает задачу отсюда, заполняет договор в уже
авторизованной сессии браузера и возвращает результат.

Очередь с арендой (lease): задача выдаётся одному исполнителю на срок
lease_expires_at; если он не отчитался, задача возвращается в очередь.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AgentTaskStatus(str, enum.Enum):
    queued = "queued"            # ждёт агента
    leased = "leased"            # выдана агенту, идёт заполнение
    done = "done"                # договор заведён
    failed = "failed"            # ошибка, будет повтор
    needs_human = "needs_human"  # повторы исчерпаны / требуется решение человека


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AgentTaskStatus] = mapped_column(
        Enum(AgentTaskStatus, name="agent_task_status"),
        default=AgentTaskStatus.queued,
        index=True,
    )

    # аренда задачи исполнителем
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # что вводить: значения чек-листа, приведённые к полям AdInsure
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # что получилось: номер договора, премия, ссылка
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
