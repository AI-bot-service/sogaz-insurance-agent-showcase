"""Эмбеддинги для RAG. Дефолт — OpenAI text-embedding-3-small (дёшево, рус.);
опция local (multilingual-e5) без внешних вызовов.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings

_openai_client = None


def _oai():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


@lru_cache
def _local_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


async def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    if settings.embedding_backend == "local":
        # e5 требует префиксы query:/passage:
        prefix = "query: " if is_query else "passage: "
        model = _local_model()
        vectors = model.encode(
            [prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True
        )
        return [v.tolist() for v in vectors]

    resp = await _oai().embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in resp.data]


async def embed_query(text: str) -> list[float]:
    return (await embed_texts([text], is_query=True))[0]
