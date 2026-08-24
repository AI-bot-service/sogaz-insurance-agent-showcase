"""Pydantic-схемы заявки для API и админки."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.application import ApplicationStatus


class ApplicationBase(BaseModel):
    object_type: str
    channel: str = "telegram"
    external_user_id: str


class ApplicationCreate(ApplicationBase):
    pass


class ApplicationUpdate(BaseModel):
    values: dict[str, Any] | None = None
    groups: dict[str, list[dict[str, Any]]] | None = None
    status: ApplicationStatus | None = None
    client_name: str | None = None
    client_phone: str | None = None


class ApplicationRead(ApplicationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ApplicationStatus
    client_name: str | None = None
    client_phone: str | None = None
    values: dict[str, Any]
    groups: dict[str, list[dict[str, Any]]]
    completion: float
    quote: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    object_type: str
    status: ApplicationStatus
    client_name: str | None = None
    client_phone: str | None = None
    completion: float
    created_at: datetime
    updated_at: datetime
