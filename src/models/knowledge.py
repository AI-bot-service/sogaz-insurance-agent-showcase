"""Чанки базы знаний для RAG (pgvector).

Документы СОГАЗ: Правила, КИД, Памятка, Полис — отдельно для квартиры и дома.
"""
from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    object_type: Mapped[str] = mapped_column(String(16), index=True)  # flat | house | common
    doc_type: Mapped[str] = mapped_column(String(32))   # pravila | kid | pamyatka | polis
    source: Mapped[str] = mapped_column(String(255))     # имя файла
    page: Mapped[int] = mapped_column(Integer, default=0)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))
