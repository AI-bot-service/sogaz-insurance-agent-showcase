"""Проверка токенов доступа к API.

В приложении два независимых круга доступа:

  * `agent`  — очередь задач для Windows-агента. Единственная часть API,
    доступная снаружи (через Traefik по префиксу пути), поэтому токен
    обязателен всегда.
  * `admin`  — заявки и чек-листы. Наружу не публикуется, но токен нужен и
    здесь: эндпоинты отдают персональные данные клиентов (ФИО, документы,
    телефон, адрес), и полагаться только на то, что порт слушает localhost,
    нельзя — любой процесс на сервере обратился бы к ним без всякой проверки.

Токены сравниваются постоянным по времени сравнением, чтобы по времени
ответа нельзя было подобрать значение посимвольно.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings

# Минимальная длина токена: 32 байта в hex. Короче — проще перебрать.
MIN_TOKEN_LENGTH = 32


def _extract_bearer(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Нужен заголовок Authorization: Bearer <токен>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _check(token: str, expected: str, realm: str) -> str:
    if not expected:
        # Пустой токен в настройках не должен превращаться в открытый доступ.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Доступ к {realm} не сконфигурирован (токен не задан).",
        )
    if len(expected) < MIN_TOKEN_LENGTH:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Токен доступа к {realm} слишком короткий — задайте не менее "
            f"{MIN_TOKEN_LENGTH} символов (openssl rand -hex 32).",
        )
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def require_agent(authorization: str = Header(default="")) -> str:
    """Доступ Windows-агента к очереди задач.

    Токенов может быть несколько — по одному на машину агента, чтобы отозвать
    доступ одной из них, не меняя настройки на остальных. Проверяются все:
    выход из перебора по первому совпадению давал бы разное время ответа.
    """
    token = _extract_bearer(authorization)
    tokens = settings.agent_tokens
    if not tokens:
        return _check(token, "", "агентскому API")

    matched = ""
    for expected in tokens:
        try:
            _check(token, expected, "агентскому API")
        except HTTPException as e:
            # Слишком короткий или пустой токен в настройках — это ошибка
            # конфигурации, а не чужой запрос: сообщаем сразу.
            if e.status_code != status.HTTP_401_UNAUTHORIZED:
                raise
        else:
            matched = token
    if not matched:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return matched


def token_fingerprint(token: str) -> str:
    """Короткий отпечаток токена для логов.

    Сам токен в лог попадать не должен: логи читают и пересылают. Отпечаток
    сверяется на месте — `echo -n "<токен>" | sha256sum` даёт то же начало.
    """
    if not token:
        return "—"
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def token_machine(token: str) -> str:
    """Имя машины, которой принадлежит токен (AGENT_MACHINES).

    Так очередь опознаёт исполнителя: имя `worker` агент присылает сам и может
    прислать любое, а токен ему выдали. Токен без имени (старый общий
    AGENT_API_TOKEN) опознаётся отпечатком — закрепление работает и на нём.
    """
    for name, expected in settings.agent_machine_map.items():
        if expected and secrets.compare_digest(token, expected):
            return name
    return f"токен:{token_fingerprint(token)}"


async def require_admin(authorization: str = Header(default="")) -> str:
    """Доступ к заявкам и чек-листам (персональные данные клиентов)."""
    return _check(_extract_bearer(authorization), settings.admin_api_token, "админскому API")
