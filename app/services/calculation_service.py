"""Единая точка пересчёта расчёта (P1-01, P0-01).

До этого модуля один и тот же заказ пересчитывался в четырёх местах
(`calculation.py`, дважды `discounts.py`, `nav.py`, `kp.py`) с разными
наборами аргументов. Отсюда три разных итога на одном заказе: экран без
скидки, БД со скидкой по 5 из 10 компонентов, КП без скидки.

Правило после P1-01: обработчик только загружает расчёт (с проверкой
владельца) и вызывает этот сервис. Все вызовы `calculate_full_cost` живут
здесь. Коэффициенты, снимок прайса и скидка загружаются в одном месте.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calculation import Calculation
from app.models.calculation_version import CalculationVersion
from app.models.price import PriceItem
from app.services.calc_engine import CalculationResult, calculate_full_cost
from app.services.calculation_persist import apply_result
from app.services.cost_calc import DiscountInfo
from app.services.price_book import load_price_book
from app.services.pricing_policy import (
    PricingPolicy,
    coerce_policy,
    load_default_policy,
)

# P1-13: ниже этих значений КП не выпускается без проверки оператора.
LOW_CONFIDENCE_LEVELS = {"low"}
QUALITY_SCORE_MIN = 0.6


def discount_info_from(calculation: Calculation) -> DiscountInfo:
    """DiscountInfo из полей расчёта — единственное место такого перевода."""
    return DiscountInfo(
        discount_type=calculation.discount_type,
        discount_value=float(calculation.discount_value or 0),
        markup_type=calculation.markup_type,
        markup_value=float(calculation.markup_value or 0),
        designer_bonus_enabled=bool(calculation.designer_bonus_enabled),
        designer_bonus_rate=float(calculation.designer_bonus_rate or 0.10),
    )


class CalculationService:
    """Оркестрация расчёта: снимок прайса, политика, вызов ядра, запись.

    Сервис создаётся на одну сессию/транзакцию. Внутри кэшируются прайс-лист
    и политика, чтобы один пересчёт не читал прайс дважды.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._price_book_cache: dict[str, float] | None = None
        self._policy_cache: PricingPolicy | None = None

    async def _get_price_book(self) -> dict[str, float]:
        if self._price_book_cache is None:
            self._price_book_cache = await load_price_book(self.session)
        return self._price_book_cache

    async def _get_policy(self) -> PricingPolicy:
        if self._policy_cache is None:
            self._policy_cache = await load_default_policy(self.session)
        return self._policy_cache

    async def preview(
        self,
        modules: list[dict] | None,
        selected_materials: dict[str, Any] | None,
        selected_hardware: dict[str, Any] | None,
        glass_items: list[dict] | None,
        *,
        discount: DiscountInfo | None = None,
        zone_type: str | None = None,
        has_spec: bool = False,
        price_book: dict[str, float] | None = None,
        policy: PricingPolicy | None = None,
    ) -> CalculationResult:
        """Полный расчёт без записи в БД (экран до/после скидки, превью).

        `price_book`/`policy` можно передать явно — так тесты и Excel
        используют тот же путь, что бот, без второго набора формул.
        """
        return calculate_full_cost(
            modules=modules or [],
            selected_materials=selected_materials or {},
            selected_hardware=selected_hardware or {},
            glass_items=glass_items or [],
            discount_info=discount,
            settings=policy or await self._get_policy(),
            zone_type=zone_type,
            price_book=(
                price_book
                if price_book is not None
                else await self._get_price_book()
            ),
            has_spec=has_spec,
        )

    async def preview_calculation(
        self,
        calculation: Calculation,
        *,
        discount: DiscountInfo | None = None,
        has_spec: bool = False,
        price_book: dict[str, float] | None = None,
        policy: PricingPolicy | None = None,
        price_mode: Literal["snapshot", "current"] = "snapshot",
    ) -> CalculationResult:
        """Превью сохранённого расчёта.

        P1-10: по умолчанию цены и коэффициенты берутся из снимка `quote`
        (сохранённый расчёт не «плывёт» от изменения прайса). `price_mode=
        "current"` — только явная команда «обновить по текущему прайсу».
        """
        stored_quote = calculation.quote if isinstance(calculation.quote, dict) else {}
        if price_mode == "current":
            # Явное «обновить по текущему прайсу»: читаем заново, без кэша
            # сервиса (кэш держит цены на момент первого пересчёта).
            if price_book is None:
                price_book = await load_price_book(self.session)
            if policy is None:
                policy = await load_default_policy(self.session)
        if price_mode == "snapshot":
            stored_book = stored_quote.get("price_book")
            if price_book is None and isinstance(stored_book, dict) and stored_book:
                price_book = dict(stored_book)
            stored_policy = stored_quote.get("policy")
            if policy is None and isinstance(stored_policy, dict) and stored_policy:
                policy = coerce_policy(stored_policy)
        return await self.preview(
            calculation.modules,
            calculation.selected_materials,
            calculation.selected_hardware,
            calculation.glass_items,
            discount=discount if discount is not None else discount_info_from(calculation),
            zone_type=calculation.zone_type,
            has_spec=has_spec,
            price_book=price_book,
            policy=policy,
        )

    @staticmethod
    def _apply_recognition_gate(
        calculation: Calculation, result: CalculationResult
    ) -> None:
        """P1-13: низкая уверенность распознавания блокирует КП.

        Ошибка распознавания (0008/0011) не должна превращаться в неверную
        смету, выпущенную клиенту. Обход — только явным подтверждением
        оператора (повторный пересчёт с исправленным составом).
        """
        confidence = str(
            getattr(calculation, "recognition_confidence", "") or ""
        ).strip().lower()
        quality = getattr(calculation, "quality_score", None)
        reason = ""
        if confidence in LOW_CONFIDENCE_LEVELS:
            reason = (
                "Низкая уверенность распознавания: проверьте состав модулей "
                "перед выпуском КП"
            )
        elif quality is not None and float(quality) < QUALITY_SCORE_MIN:
            reason = (
                f"Низкое качество распознавания (score {float(quality):.2f} < "
                f"{QUALITY_SCORE_MIN}): требуется проверка оператора"
            )
        if reason:
            result.can_issue_kp = False
            if reason not in result.warnings:
                result.warnings.append(reason)

    async def _record_version(
        self,
        calculation: Calculation,
        reason: str,
        user_id: int | None,
    ) -> CalculationVersion:
        """P1-10: сохранить неизменяемую версию снимка расчёта."""
        if calculation.id is None:
            await self.session.flush()
        last_no = await self.session.scalar(
            select(func.max(CalculationVersion.version_no)).where(
                CalculationVersion.calculation_id == calculation.id
            )
        )
        quote = dict(calculation.quote or {})
        stamp = await self.session.scalar(select(func.max(PriceItem.updated_at)))
        price_list_version = str(
            quote.get("price_list_version")
            or (stamp.isoformat() if stamp is not None else "")
            or quote.get("created_at")
            or ""
        )
        # Снимок версии не зависит от последующих правок Calculation.quote.
        version = CalculationVersion(
            calculation_id=calculation.id,
            version_no=int(last_no or 0) + 1,
            reason=reason,
            quote=copy.deepcopy(quote),
            price_list_version=price_list_version or None,
            policy_version=quote.get("policy_version"),
            engine_version=quote.get("engine_version"),
            created_by=user_id,
        )
        self.session.add(version)
        return version

    async def recalculate(
        self,
        calculation: Calculation,
        *,
        discount: DiscountInfo | None = None,
        reason: str = "manual",
        commit: bool = True,
        has_spec: bool = False,
        price_mode: Literal["snapshot", "current"] = "snapshot",
        user_id: int | None = None,
    ) -> CalculationResult:
        """Полный пересчёт расчёта и атомарная запись результата.

        Скидка берётся из полей расчёта, если не передана явно. Все компоненты
        (ЛДСП, ХДФ, кромка, фасады, фурнитура, стекло, GOLA, LED, столешница,
        крепёж) пересчитываются одним вызовом — скидка больше не может
        «потерять» половину себестоимости (P0-01).
        """
        result = await self.preview_calculation(
            calculation,
            discount=discount,
            has_spec=has_spec,
            price_mode=price_mode,
        )
        self._apply_recognition_gate(calculation, result)
        apply_result(calculation, result, reason=reason)
        await self._record_version(calculation, reason, user_id)
        if commit:
            await self.session.commit()
        return result

    async def apply_discount(
        self,
        calculation: Calculation,
        *,
        discount_type: str | None = None,
        discount_value: float = 0,
        markup_type: str | None = None,
        markup_value: float = 0,
        designer_bonus_enabled: bool | None = None,
        designer_bonus_rate: float | None = None,
        reason: str = "discount",
        commit: bool = True,
        user_id: int | None = None,
    ) -> CalculationResult:
        """Сменить скидку/наценку/бонус и пересчитать полным путём.

        Сначала считается превью с новыми значениями (там же срабатывает
        валидация `PricingError`), и только если оно прошло — поля расчёта
        меняются. Поэтому невалидная скидка не оставляет «полусостояние»
        в БД (P2-06).
        """
        new_discount = DiscountInfo(
            discount_type=discount_type,
            discount_value=float(discount_value or 0),
            markup_type=markup_type,
            markup_value=float(markup_value or 0),
            designer_bonus_enabled=(
                bool(calculation.designer_bonus_enabled)
                if designer_bonus_enabled is None
                else bool(designer_bonus_enabled)
            ),
            designer_bonus_rate=(
                float(calculation.designer_bonus_rate or 0.10)
                if designer_bonus_rate is None
                else float(designer_bonus_rate)
            ),
        )

        result = await self.preview_calculation(calculation, discount=new_discount)

        calculation.discount_type = new_discount.discount_type
        calculation.discount_value = new_discount.discount_value
        calculation.markup_type = new_discount.markup_type
        calculation.markup_value = new_discount.markup_value
        calculation.designer_bonus_enabled = 1 if new_discount.designer_bonus_enabled else 0
        calculation.designer_bonus_rate = new_discount.designer_bonus_rate
        apply_result(calculation, result, reason=reason)
        await self._record_version(calculation, reason, user_id)
        if commit:
            await self.session.commit()
        return result

    async def clear_discount(
        self,
        calculation: Calculation,
        *,
        reason: str = "discount_removed",
        commit: bool = True,
        user_id: int | None = None,
    ) -> CalculationResult:
        """Убрать скидку и наценку, сохранив бонус дизайнера."""
        return await self.apply_discount(
            calculation,
            discount_type=None,
            discount_value=0,
            markup_type=None,
            markup_value=0,
            reason=reason,
            commit=commit,
            user_id=user_id,
        )

    async def set_designer_bonus(
        self,
        calculation: Calculation,
        enabled: bool,
        *,
        reason: str = "designer_bonus",
        commit: bool = True,
        user_id: int | None = None,
    ) -> CalculationResult:
        """Включить/выключить бонус дизайнера полным пересчётом."""
        return await self.apply_discount(
            calculation,
            discount_type=calculation.discount_type,
            discount_value=float(calculation.discount_value or 0),
            markup_type=calculation.markup_type,
            markup_value=float(calculation.markup_value or 0),
            designer_bonus_enabled=enabled,
            reason=reason,
            commit=commit,
            user_id=user_id,
        )
