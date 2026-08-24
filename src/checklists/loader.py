"""Загрузчик и движок декларативных чек-листов (flat.yaml / house.yaml).

Единый источник истины по полям заявки. Используется:
  - диалог-движком (какое поле спросить следующим, с учётом depends_on);
  - оцифровкой (валидация и приведение значений из речи/фото);
  - верификацией и генерацией summary;
  - провайдерами расчёта (маппинг в формат СК).
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

CHECKLIST_DIR = Path(__file__).parent
ObjectType = Literal["flat", "house"]

FieldType = Literal[
    "text", "textarea", "number", "money", "bool", "enum", "multi", "date",
    "phone", "address", "group",
]


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass
class Field:
    id: str
    label: str
    type: FieldType
    required: bool = False
    help: str | None = None
    ai_hint: str | None = None
    options: list[Option] = dc_field(default_factory=list)
    depends_on: dict[str, Any] | None = None
    group_of: str | None = None
    # Формат текстового поля: серия паспорта — ровно четыре цифры, код
    # подразделения — 000-000. Значение, не подошедшее под шаблон, не
    # записывается: бот переспросит, а форма получит то, что примет.
    pattern: str | None = None
    format_hint: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Field":
        return cls(
            id=raw["id"],
            label=raw["label"],
            type=raw["type"],
            required=bool(raw.get("required", False)),
            help=raw.get("help"),
            ai_hint=raw.get("ai_hint"),
            options=[Option(o["value"], o["label"]) for o in raw.get("options", [])],
            depends_on=raw.get("depends_on"),
            group_of=raw.get("group_of"),
            pattern=raw.get("pattern"),
            format_hint=raw.get("format_hint"),
        )


@dataclass
class Section:
    id: str
    title: str
    fields: list[Field]
    help: str | None = None
    depends_on: dict[str, Any] | None = None


@dataclass
class Group:
    id: str
    title: str
    fields: list[Field]


@dataclass
class Checklist:
    object_type: ObjectType
    product: str
    title: str
    sections: list[Section]
    groups: dict[str, Group]

    # -- индексирование -------------------------------------------------
    def all_fields(self) -> Iterable[Field]:
        for s in self.sections:
            yield from s.fields

    def field(self, field_id: str) -> Field | None:
        for f in self.all_fields():
            if f.id == field_id:
                return f
        for g in self.groups.values():
            for f in g.fields:
                if f.id == field_id:
                    return f
        return None

    # -- условная видимость --------------------------------------------
    @staticmethod
    def _cond_met(cond: dict[str, Any] | None, values: dict[str, Any]) -> bool:
        """Проверка depends_on. Поддержка equals / contains / filled."""
        if not cond:
            return True
        target = values.get(cond["field"])
        if "filled" in cond:
            # Вопрос нужен, только если клиент назвал что-то в другом поле:
            # где хранится самоходная техника, спрашиваем лишь тогда, когда
            # она вообще страхуется. Ноль и пустая строка — «не назвал».
            return bool(target not in (None, "", 0)) == bool(cond["filled"])
        if "equals" in cond:
            return target == cond["equals"]
        if "contains" in cond:
            needle = cond["contains"]
            if isinstance(target, (list, tuple, set)):
                return needle in target
            return target == needle
        return True

    def visible_sections(self, values: dict[str, Any]) -> list[Section]:
        return [s for s in self.sections if self._cond_met(s.depends_on, values)]

    def visible_fields(self, values: dict[str, Any]) -> list[Field]:
        out: list[Field] = []
        for s in self.visible_sections(values):
            for f in s.fields:
                if self._cond_met(f.depends_on, values):
                    out.append(f)
        return out

    # -- повторяемые группы ---------------------------------------------
    def group_template(self, field: Field) -> "Group | None":
        """Шаблон элемента группы: поля одного строения, сооружения, комнаты."""
        return self.groups.get(field.group_of or "")

    def group_items(self, field: Field, groups: dict[str, Any] | None) -> list[dict]:
        return list((groups or {}).get(field.id) or [])

    def item_missing(self, field: Field, item: dict[str, Any]) -> list[Field]:
        """Обязательные поля элемента группы, которых в нём нет.

        Пустой элемент (`{}`) — это тоже пропуск: он доезжал до агента строкой
        без единого значения, и в форме СК появлялось строение без площади,
        стен и страховой суммы.
        """
        tmpl = self.group_template(field)
        if not tmpl:
            return []
        return [f for f in tmpl.fields if f.required and self._is_empty(item.get(f.id))]

    def group_gaps(
        self, values: dict[str, Any], groups: dict[str, Any] | None
    ) -> list[tuple[Field, int, list[Field]]]:
        """Пробелы в группах: (поле-группа, номер элемента с 1, чего не хватает).

        Номер 0 означает, что элементов нет вовсе, а группа обязательна.
        """
        out: list[tuple[Field, int, list[Field]]] = []
        for f in self.visible_fields(values):
            if f.type != "group":
                continue
            items = self.group_items(f, groups)
            if not items:
                if f.required:
                    out.append((f, 0, []))
                continue
            for idx, item in enumerate(items, 1):
                gaps = self.item_missing(f, item)
                if gaps:
                    out.append((f, idx, gaps))
        return out

    # -- прогресс сбора -------------------------------------------------
    def next_required_field(
        self, values: dict[str, Any], groups: dict[str, Any] | None = None
    ) -> Field | None:
        """Первое обязательное незаполненное поле с учётом видимости.

        Группы участвуют наравне с обычными полями: у дома строения —
        обязательный раздел, и пока в заявке нет ни одного строения, сбор не
        закончен. Раньше группы здесь пропускались, бот объявлял, что всё
        собрано, и заявка уходила в СК без единого строения.
        """
        for f in self.visible_fields(values):
            if f.type == "group":
                if not f.required:
                    continue
                items = self.group_items(f, groups)
                if not items or any(self.item_missing(f, it) for it in items):
                    return f
                continue
            if f.required and self._is_empty(values.get(f.id)):
                return f
        return None

    def missing_required(
        self, values: dict[str, Any], groups: dict[str, Any] | None = None
    ) -> list[Field]:
        out: list[Field] = []
        for f in self.visible_fields(values):
            if f.type == "group":
                if not f.required:
                    continue
                items = self.group_items(f, groups)
                if not items or any(self.item_missing(f, it) for it in items):
                    out.append(f)
                continue
            if f.required and self._is_empty(values.get(f.id)):
                out.append(f)
        return out

    def _group_progress(
        self, field: Field, groups: dict[str, Any] | None
    ) -> tuple[int, int]:
        """(заполнено, всего) обязательных пунктов группы для прогресса."""
        items = self.group_items(field, groups)
        tmpl = self.group_template(field)
        req = [f for f in tmpl.fields if f.required] if tmpl else []
        if not items:
            # пустая необязательная группа в счёт не идёт
            return (0, 1) if field.required else (0, 0)
        if not req:
            return 1, 1
        done = sum(
            1 for it in items for f in req if not self._is_empty(it.get(f.id))
        )
        return done, len(req) * len(items)

    def completion(
        self, values: dict[str, Any], groups: dict[str, Any] | None = None
    ) -> float:
        done = total = 0
        for f in self.visible_fields(values):
            if f.type == "group":
                d, t = self._group_progress(f, groups)
                done, total = done + d, total + t
                continue
            if not f.required:
                continue
            total += 1
            done += 0 if self._is_empty(values.get(f.id)) else 1
        if not total:
            return 1.0
        return round(done / total, 3)

    @staticmethod
    def _is_empty(v: Any) -> bool:
        return v is None or v == "" or v == [] or v == {}


def _parse(raw: dict[str, Any], object_type: ObjectType) -> Checklist:
    sections = [
        Section(
            id=s["id"],
            title=s["title"],
            help=s.get("help"),
            depends_on=s.get("depends_on"),
            fields=[Field.from_raw(f) for f in s["fields"]],
        )
        for s in raw["sections"]
    ]
    groups = {
        gid: Group(id=gid, title=g["title"], fields=[Field.from_raw(f) for f in g["fields"]])
        for gid, g in (raw.get("groups") or {}).items()
    }
    return Checklist(
        object_type=object_type,
        product=raw["product"],
        title=raw["title"],
        sections=sections,
        groups=groups,
    )


@lru_cache(maxsize=4)
def load_checklist(object_type: ObjectType) -> Checklist:
    path = CHECKLIST_DIR / f"{object_type}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _parse(raw, object_type)
