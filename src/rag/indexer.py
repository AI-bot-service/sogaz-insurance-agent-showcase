"""Индексатор базы знаний: PDF СОГАЗ → чанки → эмбеддинги → pgvector.

Структура knowledge_base/:
    flat/  01_pravila.pdf 02_kid.pdf 03_pamyatka.pdf 04_polis.pdf
    house/ 01_pravila.pdf 02_kid.pdf 03_pamyatka.pdf 04_polis.pdf

Запуск: python -m app.rag.indexer
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import fitz  # PyMuPDF
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.models.knowledge import KnowledgeChunk
from app.rag.embeddings import embed_texts

KB_DIR = Path(__file__).resolve().parents[2] / "knowledge_base"

DOC_TYPES = {
    "01_pravila": "pravila",
    "02_kid": "kid",
    "03_pamyatka": "pamyatka",
    "04_polis": "polis",
}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk(text: str) -> list[str]:
    """Простое посимвольное чанкование с перекрытием по границам абзацев."""
    text = _clean(text)
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + CHUNK_CHARS
        # попытка обрезать по концу предложения/абзаца
        window = text[start:end]
        cut = max(window.rfind("\n\n"), window.rfind(". "))
        if cut > CHUNK_CHARS * 0.5:
            end = start + cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - CHUNK_OVERLAP
    return chunks


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    doc = fitz.open(pdf_path)
    out = []
    for i, page in enumerate(doc, start=1):
        out.append((i, page.get_text("text")))
    doc.close()
    return out


async def index_object(object_type: str) -> int:
    src_dir = KB_DIR / object_type
    if not src_dir.exists():
        raise FileNotFoundError(src_dir)

    rows: list[KnowledgeChunk] = []
    payloads: list[str] = []

    for pdf in sorted(src_dir.glob("*.pdf")):
        stem = pdf.stem
        doc_type = DOC_TYPES.get(stem, "other")
        for page_no, page_text in extract_pages(pdf):
            for ci, chunk in enumerate(_chunk(page_text)):
                rows.append(
                    KnowledgeChunk(
                        object_type=object_type,
                        doc_type=doc_type,
                        source=pdf.name,
                        page=page_no,
                        chunk_index=ci,
                        content=chunk,
                        embedding=[0.0],  # заполняется ниже
                    )
                )
                payloads.append(chunk)

    if not rows:
        return 0

    # эмбеддинги пачками
    vectors: list[list[float]] = []
    BATCH = 32
    for i in range(0, len(payloads), BATCH):
        vectors.extend(await embed_texts(payloads[i : i + BATCH]))
    for row, vec in zip(rows, vectors):
        row.embedding = vec

    async with SessionLocal() as session:
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.object_type == object_type)
        )
        session.add_all(rows)
        await session.commit()

    return len(rows)


async def main() -> None:
    total = 0
    for obj in ("flat", "house"):
        n = await index_object(obj)
        print(f"[{obj}] проиндексировано чанков: {n}")
        total += n
    print(f"Итого: {total}")


if __name__ == "__main__":
    asyncio.run(main())
