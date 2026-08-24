"""Точка входа ботов: Telegram и MAX в одном процессе (long polling).

Запуск: python -m app.bot.runner

Каналы независимы: запускается тот, у которого задан токен. Если упал один —
процесс завершается целиком, и контейнер поднимает его заново (restart:
unless-stopped): молча работать половиной каналов хуже, чем перезапуститься.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.bot import maxbot
from app.bot.max_api import MaxBot, poll
from app.bot.telegram import build_dispatcher
from app.core.config import settings

logger = logging.getLogger("bot.runner")


async def run_telegram() -> None:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = build_dispatcher(bot)
    logger.info("Telegram: запуск long polling")
    await dp.start_polling(bot)


async def run_max() -> None:
    """MAX на long polling.

    Вебхук (`POST /subscriptions`) потребует публичного маршрута в Traefik и
    проверки запросов; разбор событий от транспорта не зависит — переезд
    затронет только эту функцию.
    """
    async with MaxBot(
        settings.max_bot_token,
        base_url=settings.max_api_base,
    ) as bot:
        try:
            me = await bot.get_me()
            logger.info("MAX: запуск long polling, бот @%s (id %s)", me.username, me.user_id)
        except Exception as e:  # сеть/сертификат/лимит — опрос всё равно ретраит
            logger.warning("MAX: не удалось получить данные бота (%s), продолжаю опрос", e)
        await poll(bot, maxbot.handle_update, poll_timeout=settings.max_poll_timeout)


async def main() -> None:
    logging.basicConfig(level=settings.log_level)

    tasks: dict[str, asyncio.Task] = {}
    if settings.telegram_bot_token:
        tasks["telegram"] = asyncio.create_task(run_telegram(), name="telegram")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — канал Telegram выключен")
    if settings.max_bot_token:
        tasks["max"] = asyncio.create_task(run_max(), name="max")
    else:
        logger.warning("MAX_BOT_TOKEN не задан — канал MAX выключен")

    if not tasks:
        raise RuntimeError("не задан ни один токен бота: TELEGRAM_BOT_TOKEN или MAX_BOT_TOKEN")

    # Упавший канал не тянет за собой остальные: пока жив хотя бы один,
    # клиенты продолжают писать. Иначе сбой у одного мессенджера (протухший
    # сертификат, недоступный API) молча выключал бы и второй.
    pending = set(tasks.values())
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            name = task.get_name()
            error = task.exception()
            if error:
                logger.exception("Канал %s остановлен: %s", name, error, exc_info=error)
            else:
                logger.warning("Канал %s завершился без ошибки", name)

    # Живых каналов не осталось — выходим с ошибкой, контейнер поднимется заново.
    raise RuntimeError("все каналы остановлены")


if __name__ == "__main__":
    asyncio.run(main())
