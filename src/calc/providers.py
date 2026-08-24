"""Конкретные провайдеры расчёта + фабрика по config.calc_provider."""
from __future__ import annotations

from app.calc.base import CalcProvider, QuoteResult
from app.core.config import settings
from app.models.application import Application


class ManualProvider(CalcProvider):
    """MVP: заявку считает оператор в ЛК СОГАЗ. Здесь только фиксируем передачу."""

    name = "manual"

    async def quote(self, app: Application) -> QuoteResult:
        return QuoteResult(
            status="pending_manual",
            provider=self.name,
            message=(
                "Заявка передана специалисту для расчёта в личном кабинете СОГАЗ. "
                "Стоимость сообщим в ближайшее время."
            ),
        )


class SogazApiProvider(CalcProvider):
    """Расчёт через агентский API СОГАЗ. Заготовка — эндпоинты уточняются у СК.

    Маппинг полей чек-листа → payload API вынесен в _to_payload и требует
    сверки со спецификацией СОГАЗ после получения доступа.
    """

    name = "sogaz_api"

    def _to_payload(self, app: Application) -> dict:
        # TODO: сверить с реальной спецификацией агентского API СОГАЗ
        return {
            "product": "personal_solution_plus",
            "object_type": app.object_type,
            "values": app.values,
            "groups": app.groups,
        }

    async def quote(self, app: Application) -> QuoteResult:
        if not settings.sogaz_api_base or not settings.sogaz_api_token:
            return QuoteResult(
                status="error",
                provider=self.name,
                message="API СОГАЗ не сконфигурирован (нет доступа/токена).",
            )
        import httpx

        payload = self._to_payload(app)
        headers = {"Authorization": f"Bearer {settings.sogaz_api_token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.sogaz_api_base}/calc", json=payload, headers=headers
            )
        if resp.status_code != 200:
            return QuoteResult(
                status="error", provider=self.name,
                message=f"API СОГАЗ вернул {resp.status_code}", raw={"body": resp.text},
            )
        data = resp.json()
        return QuoteResult(
            status="quoted",
            premium=data.get("premium"),
            sum_insured=data.get("sum_insured"),
            policy_draft_url=data.get("policy_draft_url"),
            provider=self.name,
            raw=data,
        )


class RpaProvider(CalcProvider):
    """Авто-заполнение ЛК/калькулятора shop.sogaz.ru браузером (Playwright).

    Этап 2. Здесь — точка расширения; сама автоматизация в rpa_runner.py.
    """

    name = "rpa"

    async def quote(self, app: Application) -> QuoteResult:
        from app.calc.rpa_runner import fill_and_quote

        return await fill_and_quote(app)


def get_provider() -> CalcProvider:
    from app.calc.agent_outbox import AgentOutboxProvider

    mapping = {
        "manual": ManualProvider,
        "sogaz_api": SogazApiProvider,
        "rpa": RpaProvider,
        "agent": AgentOutboxProvider,
    }
    return mapping.get(settings.calc_provider, ManualProvider)()
