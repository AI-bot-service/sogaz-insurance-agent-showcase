"""Формирование состояния сбора для промпта и каталога полей для LLM."""
from __future__ import annotations

from app.checklists.loader import Checklist, Field
from app.models.application import Application


def _fmt_value(field: Field, value) -> str:
    if field.type in ("enum", "multi") and field.options:
        labels = {o.value: o.label for o in field.options}
        if isinstance(value, list):
            return ", ".join(labels.get(v, str(v)) for v in value)
        return labels.get(value, str(value))
    return str(value)


def _field_line(f: Field, prefix: str = "- ") -> str:
    desc = f"{prefix}{f.id} [{f.type}]"
    if f.required:
        desc += " (обязательно)"
    desc += f": {f.label}"
    if f.options:
        opts = "; ".join(f"{o.value}={o.label}" for o in f.options)
        desc += f" | варианты: {opts}"
    # Формат поля: серия паспорта — четыре цифры, код подразделения —
    # 000-000. Без подсказки модель пишет всё одной строкой, и значение
    # отвергается проверкой формата.
    if f.format_hint:
        desc += f" | формат: {f.format_hint}"
    if f.ai_hint:
        desc += f" | подсказка: {f.ai_hint}"
    return desc


def field_catalog(checklist: Checklist, values: dict) -> str:
    """Каталог видимых полей с id/типом/опциями — чтобы LLM знал, что писать в set_fields.

    У поля-группы раскрывается шаблон элемента: без него модель не знала id
    полей строения (b_name, b_area, b_sum...) и вызывала add_group_item с
    пустым `item` — в заявке оставалось строение без единого значения.
    """
    lines: list[str] = []
    for section in checklist.visible_sections(values):
        lines.append(f"## {section.title}")
        for f in section.fields:
            if not checklist._cond_met(f.depends_on, values):
                continue
            if f.type == "group":
                tmpl = checklist.group_template(f)
                lines.append(
                    f"- {f.id} [группа]{' (обязательно)' if f.required else ''}: {f.label}"
                    f" | добавлять по одному: add_group_item(group={f.id}, item={{...}})"
                )
                if f.help:
                    lines.append(f"    пояснение: {f.help}")
                if tmpl:
                    lines.append(f"    поля одного элемента «{tmpl.title}»:")
                    for gf in tmpl.fields:
                        lines.append(_field_line(gf, prefix="    - "))
                continue
            lines.append(_field_line(f))
    return "\n".join(lines)


def _group_state(checklist: Checklist, f: Field, groups: dict) -> list[str]:
    """Состав уже добавленных элементов группы — построчно, для промпта."""
    items = checklist.group_items(f, groups)
    if not items:
        return [f"{f.id} = пока ни одного"]
    tmpl = checklist.group_template(f)
    by_id = {gf.id: gf for gf in tmpl.fields} if tmpl else {}
    out = [f"{f.id} = {len(items)} шт.:"]
    for idx, item in enumerate(items, 1):
        got = ", ".join(
            f"{k}={_fmt_value(by_id[k], v)}" if k in by_id else f"{k}={v}"
            for k, v in item.items()
        )
        out.append(f"  №{idx}: {got or 'пусто'}")
        gaps = checklist.item_missing(f, item)
        if gaps:
            out.append("     не хватает: " + ", ".join(g.id for g in gaps))
    return out


def collection_status(checklist: Checklist, app: Application) -> str:
    """Что уже заполнено, какое поле следующее, сколько осталось."""
    values = app.values or {}
    groups = app.groups or {}
    filled = []
    for f in checklist.visible_fields(values):
        if f.type == "group":
            if checklist.group_items(f, groups) or f.required:
                filled.extend(_group_state(checklist, f, groups))
            continue
        v = values.get(f.id)
        if not checklist._is_empty(v):
            filled.append(f"{f.id} = {_fmt_value(f, v)}")

    missing = checklist.missing_required(values, groups)
    nxt = checklist.next_required_field(values, groups)

    parts = [
        f"ТИП ОБЪЕКТА: {checklist.object_type} ({checklist.title})",
        f"ПРОГРЕСС: {int(checklist.completion(values, groups) * 100)}% обязательных полей собрано.",
    ]
    parts.append("ЗАПОЛНЕНО:\n" + ("\n".join(filled) if filled else "— пока ничего"))
    if nxt is not None and nxt.type == "group":
        tmpl = checklist.group_template(nxt)
        req = [gf.id for gf in tmpl.fields if gf.required] if tmpl else []
        items = checklist.group_items(nxt, groups)
        what = (
            "ни одного элемента ещё нет"
            if not items
            else "в добавленных элементах не хватает обязательных полей"
        )
        parts.append(
            f"СЛЕДУЮЩЕЕ — ГРУППА: {nxt.id} — {nxt.label} ({what}).\n"
            f"  Спроси клиента про элементы по одному и добавь каждый вызовом "
            f"add_group_item(group={nxt.id}, item={{...}}).\n"
            f"  Обязательные поля элемента: {', '.join(req) or '—'}"
        )
    elif nxt:
        opts = ""
        if nxt.options:
            opts = " | варианты: " + "; ".join(f"{o.value}={o.label}" for o in nxt.options)
        help_ = f"\n  пояснение: {nxt.help}" if nxt.help else ""
        parts.append(
            f"СЛЕДУЮЩЕЕ ПОЛЕ: {nxt.id} [{nxt.type}] — {nxt.label}{opts}{help_}"
        )
    else:
        parts.append("ВСЕ ОБЯЗАТЕЛЬНЫЕ ПОЛЯ СОБРАНЫ — переходи к проверке итога.")
    if missing:
        parts.append("ЕЩЁ НУЖНЫ (обязательные): " + ", ".join(f.id for f in missing))
    return "\n\n".join(parts)
