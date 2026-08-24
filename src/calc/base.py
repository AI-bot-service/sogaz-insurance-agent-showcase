"""Абстракция провайдера расчёта стоимости полиса и создания проекта в СК.

Реализации (config.calc_provider):
  - manual     — заявка уходит оператору в админку, он считает в ЛК СОГАЗ (MVP);
  - sogaz_api  — агентский API СОГАЗ (когда будет доступ);
  - rpa        — авто-заполнение ЛК/калькулятора через браузер (этап 2).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models.application import Application


@dataclass
class QuoteResult:
    status: str                       # quoted | pending_manual | error
    premium: float | None = None      # страховая премия, ₽
    sum_insured: float | None = None  # страховая сумма, ₽
    policy_draft_url: str | None = None
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "premium": self.premium,
            "sum_insured": self.sum_insured,
            "policy_draft_url": self.policy_draft_url,
            "provider": self.provider,
            "message": self.message,
            "raw": self.raw,
        }


class CalcProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def quote(self, app: Application) -> QuoteResult:
        """Рассчитать стоимость и создать проект полиса по собранной заявке."""
        ...
