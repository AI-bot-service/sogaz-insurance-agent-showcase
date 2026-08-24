"""FastAPI-приложение: API для админки оператора.

ПРИМЕЧАНИЕ (публичная витрина): роутеры app.api.v1.* (agent, agent_update,
applications, checklists) в этот extract не включены — они дают доступ к
персональным данным клиентов и к очереди Windows-агента, поэтому оставлены
только в приватном репозитории. Файл приведён как есть для показа общей
структуры приложения; импорт ниже в этом extract-е не разрешится.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import agent, agent_update, applications, checklists  # noqa: F401 (см. примечание выше)
from app.core.config import settings
from app.core.security import token_fingerprint

# Uvicorn настраивает только свои логгеры: без этого сообщения приложения
# (в том числе про остановленную очередь агента) не попадали в вывод контейнера
# вообще, и разбираться приходилось вслепую.
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """При старте — какие машины агента загружены и куда идут задачи.

    Дубль ключа в .env (`AGENT_MACHINES=` дважды) приложению не виден: docker
    отдаёт только последнее значение, и часть машин молча теряет доступ. Строка
    в логе показывает, что именно загрузилось, — расхождение видно сразу.
    Токены не печатаются, только отпечатки: `python3 tools/agents.py list`
    показывает те же, сверять по ним.
    """
    machines = settings.agent_machine_map
    known = ", ".join(f"{name}={token_fingerprint(t)}" for name, t in machines.items())
    legacy = len(settings.agent_tokens) - len(machines)
    logger.info(
        "Агентские машины (%s): %s%s. Очередь: %s",
        len(settings.agent_tokens),
        known or "ни одной",
        f" + без имени: {legacy}" if legacy > 0 else "",
        f"закреплена за {settings.agent_target}" if settings.agent_target
        else ("закреплена по токену" if settings.agent_target_token else "общая"),
    )
    if settings.agent_target and settings.agent_target_resolved is None:
        logger.error(
            "AGENT_TARGET=%s — машины с таким именем нет, задачи не пойдут никому",
            settings.agent_target,
        )
    yield

app = FastAPI(
    title="СОГАЗ ИИ-агент по страхованию имущества",
    version="0.1.0",
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# CORS включается, только если явно перечислены источники. Веб-кабинета у
# проекта нет, а агент ходит из Python — браузерных клиентов не существует,
# поэтому по умолчанию middleware не добавляется вовсе. Прежний вариант с «*»
# зависел от значения ENVIRONMENT: опечатка в переменной открывала API любому
# сайту, а он отдаёт персональные данные клиентов.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(applications.router, prefix="/api/v1")
app.include_router(checklists.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(agent_update.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.environment}
