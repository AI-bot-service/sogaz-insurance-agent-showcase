"""Сервис консультаций по базе знаний (RAG + LLM).

Отвечает на вопросы клиента по условиям страхования «Персональное решение+»
строго на основе документов СОГАЗ (Правила, КИД, Памятка, Полис).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retriever import format_context, search
from app.services import llm

CONSULT_SYSTEM = """Ты — консультант страховой компании СОГАЗ по продукту \
«Персональное решение+» (страхование имущества физлиц: квартиры и дома).

Правила ответа:
- Отвечай ТОЛЬКО на основе приведённого КОНТЕКСТА из официальных документов.
- Если ответа нет в контексте — честно скажи, что нужно уточнить у представителя, \
не выдумывай цифры, сроки, суммы, покрытия.
- Пиши кратко, дружелюбно, по-русски, простым языком (клиент — не юрист).
- Не давай юридических гарантий; при спорных случаях предлагай уточнить детали.
"""


async def answer(session: AsyncSession, question: str, object_type: str) -> str:
    chunks = await search(session, question, object_type, top_k=5)
    if not chunks:
        return (
            "Пока не могу найти это в документах. Уточню у представителя — "
            "или задайте вопрос иначе."
        )
    context = format_context(chunks)
    msg = await llm.chat(
        system=CONSULT_SYSTEM,
        messages=[
            llm.user_text(f"КОНТЕКСТ:\n{context}\n\nВОПРОС КЛИЕНТА:\n{question}")
        ],
        temperature=0.1,
    )
    return llm.text_of(msg)
