"""Диалог-движок: ведёт клиента по чек-листу через OpenAI tool-calling.

Цикл хода:
  1. Собираем system (роль + текущий статус сбора + каталог полей).
  2. Отдаём модели ввод клиента (текст и/или фото) + инструменты.
  3. Модель вызывает инструменты (set_object_type / set_fields / add_group_item /
     consult) — применяем их к заявке, возвращаем результат модели.
  4. Модель формулирует следующую реплику клиенту.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.state import collection_status, field_catalog
from app.checklists.loader import load_checklist
from app.digitize.coerce import CoerceError, coerce_value
from app.models.application import Application, ApplicationStatus
from app.services import address, consult, llm

SYSTEM_BASE = (Path(__file__).resolve().parents[2] / "prompts" / "dialog_system.md").read_text(
    encoding="utf-8"
)

MAX_TOOL_ITERS = 4


@dataclass
class EngineReply:
    text: str
    fields_changed: dict[str, Any] = dc_field(default_factory=dict)
    ready_for_review: bool = False


def _tools() -> list[dict[str, Any]]:
    return [
        llm.tool_def(
            "set_object_type",
            "Задать тип объекта страхования, когда стало ясно, что страхуем.",
            {
                "type": "object",
                "properties": {
                    "object_type": {"type": "string", "enum": ["flat", "house"]}
                },
                "required": ["object_type"],
            },
        ),
        llm.tool_def(
            "set_fields",
            "Записать распознанные значения полей чек-листа. Ключи — id полей из каталога.",
            {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "object",
                        "description": "Словарь {field_id: значение}. Только явно названные клиентом.",
                    }
                },
                "required": ["values"],
            },
        ),
        llm.tool_def(
            "add_group_item",
            "Добавить элемент повторяемой группы (строение дома / помещение отделки квартиры).",
            {
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "id поля-группы (building_list, fin_rooms, structure_list)"},
                    "item": {"type": "object", "description": "Словарь {field_id: значение} для одного элемента"},
                },
                "required": ["group", "item"],
            },
        ),
        llm.tool_def(
            "update_group_item",
            "Дополнить или исправить уже добавленный элемент группы (строение №1 и т.п.).",
            {
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "id поля-группы (building_list, structure_list, fin_rooms)"},
                    "index": {"type": "integer", "description": "номер элемента с 1, как в статусе сбора"},
                    "item": {"type": "object", "description": "только те поля, которые надо записать или заменить"},
                },
                "required": ["group", "index", "item"],
            },
        ),
        llm.tool_def(
            "consult",
            "Ответить на вопрос клиента об условиях страхования по официальным документам.",
            {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        ),
    ]


def _build_system(app: Application) -> str:
    if not app.object_type:
        return (
            SYSTEM_BASE
            + "\n\n---\nТИП ОБЪЕКТА ЕЩЁ НЕ ВЫБРАН. Сначала определи: квартира или дом."
        )
    checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
    status = collection_status(checklist, app)
    catalog = field_catalog(checklist, app.values or {})
    return (
        f"{SYSTEM_BASE}\n\n---\nСТАТУС СБОРА:\n{status}\n\n---\n"
        f"КАТАЛОГ ПОЛЕЙ (id для set_fields):\n{catalog}"
    )


async def _apply_set_fields(
    app: Application, values: dict[str, Any]
) -> tuple[dict, list[str], list[str]]:
    """Привести и записать значения. Вернуть (применённые, ошибки, замечания)."""
    checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
    applied: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    new_values = dict(app.values or {})
    for fid, raw in values.items():
        field = checklist.field(fid)
        if field is None:
            errors.append(f"{fid}: неизвестное поле")
            continue
        try:
            val = coerce_value(field, raw)
        except CoerceError as e:
            errors.append(f"{fid}: {e}")
            continue
        if field.type == "address" and val:
            val = await _normalize_address(new_values, fid, str(val), notes)
        new_values[fid] = val
        applied[fid] = val
    app.values = new_values
    app.completion = checklist.completion(new_values, app.groups or {})
    return applied, errors, notes


async def _normalize_address(
    values: dict[str, Any], field_id: str, raw: str, notes: list[str]
) -> str:
    """Адрес клиента → каноническая строка ФИАС (DaData).

    В заявку идёт вид, в котором адрес показывает справочник портала: иначе
    подсказка в AdInsure не находится, «Стандартизация» не разбирает строку и
    адрес в договоре остаётся пустым. Ключа нет или сервис молчит — оставляем
    сказанное клиентом: остановить сбор из-за этого нельзя.

    Метаданные (код ФИАС, что назвал клиент) кладём в служебный ключ `_addr`:
    поля чек-листа для них нет, а оператору и агенту они помогают понять,
    откуда взялся адрес в договоре.
    """
    result = await address.normalize(raw)
    if result is None:
        if address.enabled():
            notes.append(
                f"{field_id}: адрес не распознан справочником — уточните его у клиента "
                "(город, улица, дом)"
            )
        return raw

    meta = dict(values.get("_addr") or {})
    meta[field_id] = {
        "input": raw,
        "value": result.value,
        "fias_id": result.fias_id,
        "fias_level": result.fias_level,
        "postal_code": result.postal_code,
    }
    values["_addr"] = meta

    if not result.has_house:
        notes.append(
            f"{field_id}: в адресе нет номера дома («{result.value}») — спросите его у клиента"
        )
    return result.value


def _clean_group_item(
    checklist, field, group: str, item: dict[str, Any]
) -> tuple[dict, list[str]]:
    """Привести значения одного элемента группы к типам полей шаблона."""
    tmpl = checklist.group_template(field)
    by_id = {f.id: f for f in tmpl.fields}
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for k, v in item.items():
        f = by_id.get(k)
        if not f:
            errors.append(f"{group}.{k}: неизвестное поле")
            continue
        try:
            clean[k] = coerce_value(f, v)
        except CoerceError as e:
            errors.append(f"{group}.{k}: {e}")
    return clean, errors


def _group_field(app: Application, group: str):
    checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
    field = checklist.field(group)
    if not field or field.type != "group" or not field.group_of:
        return checklist, None, [f"{group}: не группа"]
    if not checklist.group_template(field):
        return checklist, None, [f"{group}: нет шаблона"]
    return checklist, field, []


def _apply_group_item(app: Application, group: str, item: dict[str, Any]) -> tuple[dict, list[str]]:
    """Добавить элемент группы: строение дома, сооружение, комнату отделки.

    Элемент без обязательных полей не добавляется. Раньше вызов с пустым
    `item` создавал строку-пустышку (`building_list: [{}]`): в форме СК
    появлялось строение без типа, площади и страховой суммы, а бот считал
    раздел собранным.
    """
    checklist, field, errors = _group_field(app, group)
    if field is None:
        return {}, errors
    clean, errors = _clean_group_item(checklist, field, group, item)
    gaps = checklist.item_missing(field, clean)
    if gaps:
        return {}, [
            *errors,
            f"{group}: элемент не добавлен — сначала спросите у клиента "
            + ", ".join(f"{g.id} ({g.label})" for g in gaps),
        ]
    groups = dict(app.groups or {})
    groups.setdefault(group, [])
    groups[group] = [*groups[group], clean]
    app.groups = groups
    return clean, errors


def _update_group_item(
    app: Application, group: str, index: int, item: dict[str, Any]
) -> tuple[dict, list[str]]:
    """Дополнить уже добавленный элемент группы.

    Клиент вспоминает подробности по ходу разговора («у бани, кстати, площадь
    20»), и без этого модели пришлось бы заводить строение заново — в заявке
    появлялся дубль.
    """
    checklist, field, errors = _group_field(app, group)
    if field is None:
        return {}, errors
    items = checklist.group_items(field, app.groups)
    if not 1 <= index <= len(items):
        return {}, [f"{group}: нет элемента №{index} (всего {len(items)})"]
    clean, errors = _clean_group_item(checklist, field, group, item)
    merged = {**items[index - 1], **clean}
    groups = dict(app.groups or {})
    groups[group] = [*items[: index - 1], merged, *items[index:]]
    app.groups = groups
    return merged, errors


async def handle_turn(
    session: AsyncSession,
    app: Application,
    *,
    text: str,
    images: list[tuple[bytes, str]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> EngineReply:
    """Обработать один ход клиента. images: [(bytes, mime), ...]."""
    if images:
        first, mime = images[0]
        user_msg = llm.user_image(text or "Вот фото по объекту страхования.", first, mime)
    else:
        user_msg = llm.user_text(text)

    messages: list[dict[str, Any]] = [*(history or []), user_msg]
    changed: dict[str, Any] = {}

    for _ in range(MAX_TOOL_ITERS):
        system = _build_system(app)
        msg = await llm.chat(system=system, messages=messages, tools=_tools())
        calls = llm.tool_calls(msg)

        if not calls:
            reply_text = llm.text_of(msg)
            break

        # добавляем ассистентское сообщение с вызовами в контекст
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (msg.tool_calls or [])
                ],
            }
        )

        for tc, (name, args) in zip(msg.tool_calls or [], calls):
            result = await _run_tool(session, app, name, args, changed)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)}
            )
    else:
        reply_text = "Записал. Продолжим — уточните, пожалуйста, следующий пункт."

    # готовность к проверке
    ready = False
    if app.object_type:
        checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
        ready = (
            checklist.next_required_field(app.values or {}, app.groups or {}) is None
            and bool(app.values)
        )
        if ready and app.status == ApplicationStatus.collecting:
            app.status = ApplicationStatus.verifying

    return EngineReply(text=reply_text, fields_changed=changed, ready_for_review=ready)


async def _run_tool(
    session: AsyncSession,
    app: Application,
    name: str,
    args: dict[str, Any],
    changed: dict[str, Any],
) -> dict[str, Any]:
    if name == "set_object_type":
        ot = args.get("object_type")
        if ot in ("flat", "house"):
            app.object_type = ot
            return {"ok": True, "object_type": ot}
        return {"ok": False, "error": "object_type должен быть flat или house"}

    if name == "set_fields":
        if not app.object_type:
            return {"ok": False, "error": "сначала выберите тип объекта (set_object_type)"}
        applied, errors, notes = await _apply_set_fields(app, args.get("values", {}))
        changed.update(applied)
        result: dict[str, Any] = {
            "ok": not errors, "applied": applied, "errors": errors,
            "completion": app.completion,
        }
        if notes:
            # Замечания по адресу: значение записано, но клиента надо
            # переспросить — модель обязана это сделать в следующей реплике.
            result["notes"] = notes
        return result

    if name == "add_group_item":
        if not app.object_type:
            return {"ok": False, "error": "сначала выберите тип объекта"}
        applied, errors = _apply_group_item(app, args.get("group", ""), args.get("item", {}))
        if applied:
            changed[args.get("group", "")] = applied
        return {"ok": not errors, "applied": applied, "errors": errors}

    if name == "update_group_item":
        if not app.object_type:
            return {"ok": False, "error": "сначала выберите тип объекта"}
        applied, errors = _update_group_item(
            app, args.get("group", ""), int(args.get("index") or 0), args.get("item", {})
        )
        if applied:
            changed[args.get("group", "")] = applied
        return {"ok": not errors, "applied": applied, "errors": errors}

    if name == "consult":
        if not app.object_type:
            return {"ok": True, "answer": "Уточните сначала — квартира или дом?"}
        ans = await consult.answer(session, args.get("question", ""), app.object_type)
        return {"ok": True, "answer": ans}

    return {"ok": False, "error": f"неизвестный инструмент {name}"}
