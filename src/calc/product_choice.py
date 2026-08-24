"""Выбор продукта по объекту, страховой сумме и региону.

Правило заказчика:
  • дом  — всегда «Персональное решение +», коробки для домов не существует;
  • квартира в Крыму       — коробка при сумме до 15 млн включительно,
                             свыше — «Персональное решение +»;
  • квартира в прочих регионах — коробка до 25 млн включительно,
                             свыше — «Персональное решение +».

Продукт определяется на сервере, а не в форме: от него зависит и адрес формы
AdInsure, и набор полей, и процесс оформления (у коробки сначала котировка).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("product_choice")

PERSONAL = "personal_solution"
BOX = "sogaz_flat_box"

LIMIT_DEFAULT = 25_000_000
LIMIT_CRIMEA = 15_000_000

# Крымский лимит ниже общего. Севастополь — отдельный субъект РФ, но
# географически тот же полуостров, поэтому попадает под то же ограничение.
CRIMEA_MARKERS = ("крым", "севастополь", "симферопол", "ялт", "керч", "евпатор",
                  "феодос", "судак", "алушт", "бахчисар", "джанко", "саки")


def is_crimea(address: str | None) -> bool:
    """Относится ли адрес к Крыму. Разбор грубый — по названиям в строке."""
    text = (address or "").lower()
    return any(marker in text for marker in CRIMEA_MARKERS)


def sum_limit(address: str | None) -> int:
    """Предельная страховая сумма, при которой коробка ещё доступна."""
    return LIMIT_CRIMEA if is_crimea(address) else LIMIT_DEFAULT


def _to_number(value: Any) -> float | None:
    """Сумма из ответа клиента: «2 500 000 руб.» → 2500000.0."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    digits = re.sub(r"[^\d.,]", "", str(value)).replace(",", ".")
    # разделители разрядов уже вырезаны, точка осталась только десятичная
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def choose_product(object_type: str | None, values: dict[str, Any]) -> str:
    """Какой продукт оформлять по этой заявке.

    Сумма и адрес берутся из ответов чек-листа. Если суммы нет — берётся
    «Персональное решение +»: оно покрывает любой случай, тогда как коробка
    сверх лимита будет отклонена уже в СОГАЗ.
    """
    if object_type != "flat":
        return PERSONAL

    total = _to_number(values.get("sum_insured_total"))
    if total is None:
        logger.info("Страховая сумма не указана — беру «Персональное решение +»")
        return PERSONAL

    address = values.get("obj_address")
    limit = sum_limit(address)
    product = BOX if total <= limit else PERSONAL

    logger.info(
        "Квартира: сумма %s, лимит коробки %s (%s) → %s",
        int(total), limit, "Крым" if is_crimea(address) else "прочие регионы", product,
    )
    return product
