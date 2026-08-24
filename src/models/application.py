"""Модель заявки на страхование.

Значения полей хранятся в JSONB (values), структура задаётся чек-листом
(flat.yaml / house.yaml) — единый источник истины, без дублирования колонок.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ApplicationStatus(str, enum.Enum):
    collecting = "collecting"       # идёт сбор данных
    verifying = "verifying"         # клиент подтверждает собранное
    ready = "ready"                 # данные собраны и подтверждены
    calculating = "calculating"     # ушло на расчёт в СК
    quoted = "quoted"               # получена стоимость/проект полиса
    issued = "issued"               # полис оформлен
    rejected = "rejected"           # отказ
    abandoned = "abandoned"         # клиент бросил


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_type: Mapped[str] = mapped_column(String(16))  # flat | house
    # продукт СК: personal_solution («Персональное решение +») |
    # sogaz_flat_box (коробка «СОГАЗ Квартира»). Определяет форму в AdInsure.
    product: Mapped[str] = mapped_column(String(32), default="personal_solution")
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"),
        default=ApplicationStatus.collecting,
        index=True,
    )

    # канал и клиент
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    external_user_id: Mapped[str] = mapped_column(String(128), index=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # собранные данные по чек-листу
    values: Mapped[dict] = mapped_column(JSONB, default=dict)
    # повторяемые группы (строения дома, комнаты отделки): {field_id: [ {..}, ..]}
    groups: Mapped[dict] = mapped_column(JSONB, default=dict)
    completion: Mapped[float] = mapped_column(Float, default=0.0)

    # результат расчёта
    quote: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
