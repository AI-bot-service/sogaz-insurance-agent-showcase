"""Конфигурация приложения (Pydantic Settings v2)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- окружение ---
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # --- БД / кэш ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/sogaz"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- LLM (диалог, vision, оцифровка) ---
    # Один провайдер OpenAI на всё: чат+vision, STT, эмбеддинги.
    # OpenRouter оставлен опцией (llm_provider=openrouter) на будущее.
    llm_provider: str = Field(default="openai")  # openai | openrouter
    openai_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")
    openrouter_base: str = Field(default="https://openrouter.ai/api/v1")
    # gpt-4o-mini: vision + tool-calling, дёшево ($0.15/$0.60 за 1M):
    llm_model: str = Field(default="gpt-4o-mini")
    llm_max_tokens: int = Field(default=2048)

    # --- Эмбеддинги для RAG ---
    embedding_backend: str = Field(default="openai")  # openai | local
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dim: int = Field(default=1536)

    # --- STT (распознавание речи) ---
    stt_model: str = Field(default="whisper-1")

    # --- Telegram ---
    telegram_bot_token: str = Field(default="")
    telegram_use_webhook: bool = Field(default=False)
    telegram_webhook_base: str = Field(default="")

    # --- MAX (мессенджер) ---
    # Токен бота из настроек MAX. Пусто — канал не запускается, Telegram
    # работает сам по себе.
    max_bot_token: str = Field(default="")
    # Адрес Bot API вынесен в настройку: MAX уже менял домен
    # (botapi.max.ru → platform-api2.max.ru), и переезд не должен требовать
    # пересборки образа.
    max_api_base: str = Field(default="https://platform-api2.max.ru")
    # Сколько секунд сервер держит соединение, пока нет событий.
    max_poll_timeout: int = Field(default=30)

    # --- Нормализация адреса (DaData) ---
    # Подсказки, а не платная стандартизация: каноническая строка ФИАС и уровень
    # точности есть и там, 10 000 запросов в сутки бесплатны. Пусто — адрес
    # уходит в заявку так, как его назвал клиент.
    dadata_api_key: str = Field(default="")
    dadata_suggest_url: str = Field(
        default="https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
    )
    dadata_timeout: float = Field(default=5.0)

    # --- Расчёт полиса ---
    calc_provider: str = Field(default="manual")  # manual | sogaz_api | rpa | agent
    sogaz_api_base: str = Field(default="")
    sogaz_api_token: str = Field(default="")
    sogaz_agent_login: str = Field(default="")
    sogaz_agent_password: str = Field(default="")

    # --- Ввод договоров в AdInsure внешним Windows-агентом (calc_provider=agent) ---
    # Портал закрыт для сервера по IP и требует 2FA: заполняет агент на машине
    # страхового агента, авторизацию проходит человек. Пароль от портала на
    # сервере не хранится — только токен доступа к нашему же API.
    agent_api_token: str = Field(default="")

    # Машины агента одной строкой: «имя:токен» через запятую. Имя нужно, чтобы
    # закреплять очередь (AGENT_TARGET) и читать логи, не сверяя 64 символа
    # глазами; правится через `python3 tools/agents.py`, а не руками.
    # Строкой, а не списком/словарём: pydantic-settings разбирает такие поля как
    # JSON раньше любых валидаторов, и обычное «имя:токен,имя:токен» из .env
    # валит запуск приложения ошибкой разбора поля. Разбор — в `agent_machines`.
    agent_machines: str = Field(default="")

    # Прежняя схема без имён: основной токен + список через запятую. Оставлены
    # рабочими, чтобы старый .env не ронял доступ; новые машины заводятся в
    # AGENT_MACHINES.
    agent_api_tokens_extra: str = Field(default="")

    # Закрепить очередь за одной машиной — её имя из AGENT_MACHINES. Пока имя
    # здесь стоит, задачи выдаются только ей, остальные агенты получают пустую
    # очередь. Пусто — задачи разбирает кто угодно.
    agent_target: str = Field(default="")

    # То же закрепление по токену — прежний способ, для .env без имён. Работает,
    # только если AGENT_TARGET пуст.
    #
    # Токен, а не имя `worker`: имя агент присылает сам и может назваться как
    # угодно, а токен уже проверен на входе и однозначно указывает на машину.
    agent_target_token: str = Field(default="")

    @property
    def agent_machine_map(self) -> dict[str, str]:
        """Машины агента: имя → токен. Записи без имени или токена отброшены."""
        machines: dict[str, str] = {}
        for item in self.agent_machines.split(","):
            name, _, token = item.partition(":")
            name, token = name.strip(), token.strip()
            if name and token:
                machines[name] = token
        return machines

    @property
    def agent_tokens(self) -> tuple[str, ...]:
        """Все действующие агентские токены, пустые значения отброшены.

        Порядок сохранён, повторы убраны: один и тот же токен, записанный и в
        AGENT_MACHINES, и в старых переменных, — это одна машина, а не две.
        """
        extra = [t.strip() for t in self.agent_api_tokens_extra.split(",")]
        seen: dict[str, None] = {}
        for token in (self.agent_api_token, *extra, *self.agent_machine_map.values()):
            if token:
                seen.setdefault(token, None)
        return tuple(seen)

    @property
    def agent_target_resolved(self) -> str | None:
        """Токен машины, за которой закреплена очередь.

        Пустая строка — очередь общая. `None` — в AGENT_TARGET стоит имя,
        которого нет в AGENT_MACHINES: предъявить такой токен некому, и очередь
        встала бы молча, поэтому вызывающий обязан сказать об этом в лог.
        """
        name = self.agent_target.strip()
        if not name:
            return self.agent_target_token.strip()
        return self.agent_machine_map.get(name)

    # --- Доступ к админскому API (заявки, чек-листы) ---
    # Эндпоинты отдают персональные данные клиентов, поэтому закрыты токеном
    # даже при работе только на localhost. Наружу не публикуются.
    admin_api_token: str = Field(default="")

    # Каталог с файлами Windows-агента для раздачи обновлений.
    # Монтируется в контейнер read-only, в образ не копируется.
    agent_dist_dir: str = Field(default="/app/agent_dist")

    # Источники для CORS. Пусто (по умолчанию) — заголовки CORS не выдаются
    # вообще: браузерных клиентов у API нет. Задавать списком через запятую
    # и только конкретные адреса, никаких «*».
    cors_origins: list[str] = Field(default_factory=list)

    # --- Этап 2: CRM ---
    amocrm_base_url: str = Field(default="")
    amocrm_access_token: str = Field(default="")
    lptracker_token: str = Field(default="")

    @property
    def is_prod(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
