"""RPA-автозаполнение личного кабинета/калькулятора СОГАЗ (п.5.1 ТЗ).

Имитирует ручной ввод данных заявки в shop.sogaz.ru через браузер (Playwright),
забирает рассчитанную стоимость и ссылку на проект полиса. Аналог «Секретарь.рф».

ВАЖНО: реальные URL, шаги авторизации и CSS-селекторы формы уточняются после
получения доступа к ЛК/агентскому кабинету СОГАЗ. Ниже — каркас с картой полей;
заполните FLAT_FIELD_MAP / HOUSE_FIELD_MAP и шаги навигации по факту.

Зависимость: pip install '.[rpa]' && playwright install chromium
"""
from __future__ import annotations

import logging
from typing import Any

from app.calc.base import QuoteResult
from app.core.config import settings
from app.models.application import Application

logger = logging.getLogger("rpa")

# URL калькулятора/ЛК по типу объекта (уточнить по доступу)
CALC_URLS = {
    "flat": "https://shop.sogaz.ru/realty/flat_personal/",
    "house": "https://shop.sogaz.ru/realty/house_personal/",
}

# Карта: id поля чек-листа -> CSS-селектор поля в форме СОГАЗ.
# TODO: заполнить реальными селекторами после осмотра формы ЛК.
FLAT_FIELD_MAP: dict[str, str] = {
    # "obj_address": "input[name='address']",
    # "obj_area_total": "input[name='area']",
    # "sum_insured_total": "input[name='sum_insured']",
}
HOUSE_FIELD_MAP: dict[str, str] = {}


async def fill_and_quote(app: Application) -> QuoteResult:
    """Открыть форму, заполнить поля, дождаться расчёта, вернуть результат."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return QuoteResult(
            status="error",
            provider="rpa",
            message="Playwright не установлен (pip install '.[rpa]').",
        )

    url = CALC_URLS.get(app.object_type or "")
    field_map = FLAT_FIELD_MAP if app.object_type == "flat" else HOUSE_FIELD_MAP
    if not url:
        return QuoteResult(status="error", provider="rpa", message="Неизвестный тип объекта")
    if not field_map:
        return QuoteResult(
            status="pending_manual",
            provider="rpa",
            message=(
                "Карта полей формы СОГАЗ ещё не настроена (нужен доступ к ЛК для "
                "снятия селекторов). Заявка передана оператору."
            ),
        )

    values: dict[str, Any] = app.values or {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            await _login_if_needed(page)
            for field_id, selector in field_map.items():
                val = values.get(field_id)
                if val is None:
                    continue
                await _set_field(page, selector, val)
            premium = await _read_premium(page)
            draft_url = await _read_draft_url(page)
            return QuoteResult(
                status="quoted" if premium else "pending_manual",
                premium=premium,
                policy_draft_url=draft_url,
                provider="rpa",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("RPA-ошибка")
            return QuoteResult(status="error", provider="rpa", message=str(e))
        finally:
            await browser.close()


async def _login_if_needed(page) -> None:
    """Авторизация в агентском ЛК, если требуется (уточнить шаги)."""
    if not settings.sogaz_agent_login:
        return
    # TODO: реализовать по факту формы логина ЛК
    return


async def _set_field(page, selector: str, value: Any) -> None:
    """Заполнить одно поле (input/select/checkbox) по селектору."""
    el = page.locator(selector).first
    tag = await el.evaluate("e => e.tagName.toLowerCase()")
    if tag == "select":
        await el.select_option(label=str(value))
    elif isinstance(value, bool):
        if value:
            await el.check()
    else:
        await el.fill(str(value))


async def _read_premium(page) -> float | None:
    """Считать рассчитанную премию (уточнить селектор итоговой суммы)."""
    return None


async def _read_draft_url(page) -> str | None:
    return None
