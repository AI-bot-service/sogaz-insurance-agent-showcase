"""Приведение и валидация значений полей чек-листа.

Значения приходят от LLM (из текста/голоса/фото) в свободной форме — здесь они
приводятся к типу поля и проверяются против опций enum/multi. Гарантирует
точность оцифровки (критерий успеха из ТЗ).

Здесь же значение приводится к виду, который примет форма СК: дата — к ISO
(агент переведёт её в ДД.ММ.ГГГГ), телефон — к +7XXXXXXXXXX, серия/номер
паспорта — к количеству цифр, которое ждёт карточка контрагента. Что не удалось
привести — не записывается: бот переспросит клиента, и это дешевле, чем
недозаполненный договор на боевом портале.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.checklists.loader import Field

TRUE_WORDS = {"да", "true", "есть", "имеется", "yes", "1", "верно", "конечно"}
FALSE_WORDS = {"нет", "false", "отсутствует", "no", "0", "не имеется"}

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


class CoerceError(ValueError):
    pass


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise CoerceError(f"ожидалось да/нет, получено: {v!r}")


def _to_number(v: Any) -> float | int:
    if isinstance(v, (int, float)):
        return v
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        raise CoerceError(f"не число: {v!r}")
    num = float(s)
    return int(num) if num.is_integer() else num


def _to_money(v: Any) -> int:
    """Деньги в рублях → целое. Поддержка '1 200 000', '1.2 млн'."""
    s = str(v).lower()
    mult = 1
    if "млн" in s or "миллион" in s:
        mult = 1_000_000
    elif "тыс" in s or "к" == s.strip()[-1:]:
        mult = 1_000
    s = s.replace(",", ".")
    m = re.search(r"[\d.\s ]+", s)
    if not m:
        raise CoerceError(f"не сумма: {v!r}")
    num = float(re.sub(r"[\s ]", "", m.group()))
    return int(num * mult)


def _to_date(v: Any) -> str:
    """Дата в любом виде → ISO ГГГГ-ММ-ДД.

    Хранение в ISO, а не как сказал клиент: карточка контрагента и поля
    договора принимают только ДД.ММ.ГГГГ, и агент переводит дату сам. Пока
    здесь лежала свободная строка, «1 марта 1985» уезжало в форму как есть и
    поле оставалось пустым.
    """
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip().lower().replace("г.", " ").strip(" .,")

    if m := re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s):
        y, mo, d = (int(x) for x in m.groups())
    elif m := re.match(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$", s):
        d, mo, y = (int(x) for x in m.groups())
    elif m := re.match(r"^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$", s):
        d, month_name, y = int(m.group(1)), m.group(2), int(m.group(3))
        mo = MONTHS.get(month_name[:3])
        if not mo:
            raise CoerceError(f"не понял месяц: {v!r}")
    else:
        raise CoerceError(
            f"дата должна быть в виде ДД.ММ.ГГГГ, получено: {v!r}"
        )

    try:
        return date(y, mo, d).isoformat()
    except ValueError as e:
        raise CoerceError(f"такой даты не существует: {v!r} ({e})") from e


def _to_phone(v: Any) -> str:
    """Телефон → +7XXXXXXXXXX. Иначе форма запишет номер как попало."""
    digits = re.sub(r"\D", "", str(v))
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise CoerceError(f"нужен телефон из 10 цифр после кода страны, получено: {v!r}")
    return f"+7{digits}"


def _match_option(field: Field, v: Any) -> str:
    """Сопоставить значение с value/label опции (регистронезависимо)."""
    s = str(v).strip().lower()
    for opt in field.options:
        if s == opt.value.lower() or s == opt.label.lower():
            return opt.value
    # частичное совпадение по label
    for opt in field.options:
        if s in opt.label.lower() or opt.label.lower() in s:
            return opt.value
    raise CoerceError(
        f"'{v}' не входит в допустимые варианты: "
        + ", ".join(o.label for o in field.options)
    )


def _check_pattern(field: Field, value: str) -> str:
    """Проверить текст по шаблону поля (серия, номер, код подразделения).

    Клиент диктует паспорт как придётся, и раньше строка уезжала в форму
    целиком. Теперь несовпадение — повод переспросить, а не испортить карточку.
    """
    if not field.pattern:
        return value
    if not re.fullmatch(field.pattern, value):
        raise CoerceError(
            f"'{value}' не подходит под формат поля"
            + (f" ({field.format_hint})" if field.format_hint else "")
        )
    return value


def coerce_value(field: Field, raw: Any) -> Any:
    """Привести сырое значение к типу поля. Бросает CoerceError при несоответствии."""
    if raw is None or raw == "":
        return None
    t = field.type
    if t in ("text", "textarea", "address"):
        # Адрес здесь только очищается от лишних пробелов: к виду справочника
        # ФИАС его приводит `services.address` — там нужен сетевой запрос.
        return _check_pattern(field, " ".join(str(raw).split()))
    if t == "bool":
        return _to_bool(raw)
    if t == "number":
        return _to_number(raw)
    if t == "money":
        return _to_money(raw)
    if t == "date":
        return _to_date(raw)
    if t == "phone":
        return _to_phone(raw)
    if t == "enum":
        return _match_option(field, raw)
    if t == "multi":
        items = raw if isinstance(raw, list) else [raw]
        return [_match_option(field, x) for x in items]
    raise CoerceError(f"неизвестный тип поля: {t}")
