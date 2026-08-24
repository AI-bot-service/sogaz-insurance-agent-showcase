"""Поиск релевантных фрагментов базы знаний (pgvector, cosine)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk
from app.rag.embeddings import embed_query


@dataclass
class RetrievedChunk:
    content: str
    doc_type: str
    source: str
    page: int
    score: float


async def search(
    session: AsyncSession,
    query: str,
    object_type: str,
    *,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Топ-k чанков для объекта (flat/house) + общих (common)."""
    qvec = await embed_query(query)
    distance = KnowledgeChunk.embedding.cosine_distance(qvec)
    stmt = (
        select(KnowledgeChunk, distance.label("dist"))
        .where(KnowledgeChunk.object_type.in_([object_type, "common"]))
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await session.execute(stmt)).all()
    return [
        RetrievedChunk(
            content=ch.content,
            doc_type=ch.doc_type,
            source=ch.source,
            page=ch.page,
            score=round(1 - float(dist), 4),
        )
        for ch, dist in rows
    ]


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Собрать контекст для промпта консультации со ссылками на документ/страницу."""
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(f"[{i}] ({c.doc_type}, {c.source} стр.{c.page})\n{c.content}")
    return "\n\n".join(parts)
