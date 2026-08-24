"""Верификация собранных данных и генерация summary для клиента.

Реализует п.3 ТЗ (перепроверка) и критерий «полное совпадение ручного и ИИ-ввода»:
итоговая сводка показывается клиенту на подтверждение перед расчётом.
"""
from __future__ import annotations

from app.checklists.loader import Field, load_checklist
from app.models.application import Application


def _fmt(field: Field, value) -> str:
    if field.type in ("enum", "multi") and field.options:
        labels = {o.value: o.label for o in field.options}
        if isinstance(value, list):
            return ", ".join(labels.get(v, str(v)) for v in value)
        return labels.get(value, str(value))
    if field.type == "bool":
        return "да" if value else "нет"
    if field.type == "money":
        return f"{int(value):,} ₽".replace(",", " ")
    return str(value)


def build_summary(app: Application) -> str:
    """Читаемая сводка заявки по секциям — для показа клиенту и оператору."""
    checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
    values = app.values or {}
    groups = app.groups or {}
    lines: list[str] = [f"📋 {checklist.title} — «{checklist.product}»", ""]

    for section in checklist.visible_sections(values):
        section_lines: list[str] = []
        for f in section.fields:
            if not checklist._cond_met(f.depends_on, values):
                continue
            if f.type == "group":
                items = groups.get(f.id, [])
                if not items:
                    continue
                tmpl = checklist.groups.get(f.group_of or "")
                by_id = {gf.id: gf for gf in tmpl.fields} if tmpl else {}
                for idx, item in enumerate(items, 1):
                    section_lines.append(f"  • {f.label} №{idx}:")
                    for k, v in item.items():
                        gf = by_id.get(k)
                        label = gf.label if gf else k
                        val = _fmt(gf, v) if gf else str(v)
                        section_lines.append(f"      – {label}: {val}")
                continue
            v = values.get(f.id)
            if checklist._is_empty(v):
                continue
            section_lines.append(f"  • {f.label}: {_fmt(f, v)}")
        if section_lines:
            lines.append(f"▸ {section.title}")
            lines.extend(section_lines)
            lines.append("")

    return "\n".join(lines).strip()


def missing_report(app: Application) -> list[str]:
    checklist = load_checklist(app.object_type)  # type: ignore[arg-type]
    return [
        f.label
        for f in checklist.missing_required(app.values or {}, app.groups or {})
    ]
