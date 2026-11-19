"""Единая политика ценообразования (P1-09, этап 2).

До этого коэффициенты жили в трёх местах: `config.Settings`, модель
`CalcSettings` и захардкоженный словарь в `calc_engine`. Изменение `.env` не
влияло на расчёт, потому что все вызовы передавали `settings=None`.

Теперь источник один: `PricingPolicy`. Он загружается из профиля `CalcSettings`
(если в БД есть активный профиль по умолчанию), иначе — из `config.py`.
Чат, Excel и КП получают один и тот же объект.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


@dataclass(frozen=True)
class PricingPolicy:
    """Коэффициенты и фиксированные сборы расчёта."""

    manufacturing_rate: float = 0.80
    installation_rate: float = 0.15
    design_rate: float = 0.10
    overhead_rate: float = 0.05
    profit_rate: float = 0.35
    tech_director_rate: float = 0.015
    manager_rate: float = 0.0
    designer_rate: float = 0.0
    measurement_fee: float = 2500.0
    delivery_fee: float = 6500.0
    delivery_fee_client: float = 10000.0
    noncash_markup: float = 1.10
    # P1-16: подтверждено собственником 2026-11-17 — маржа не ниже 15 %
    # над полными затратами, скидка без согласования — до 10 %.
    min_margin_rate: float = 0.15
    max_discount_percent: float = 10.0
    # P2-2: запас к занятой доле листа (брак/подрезка) — 30 %.
    sheet_reserve_rate: float = 0.30
    # Запас на возможные ошибки (распознавание/состав). 0 — выключен.
    risk_reserve_rate: float = 0.0
    # P1-10: версия политики — попадает в снимок и версию расчёта.
    version: str = "1.0"
    designer_bonus_rate: float = 0.10
    sheet_utilization: float = 0.85

    @classmethod
    def from_config(cls) -> PricingPolicy:
        """Профиль из `.env`/`config.py` (fallback, если в БД профиля нет)."""
        return cls(
            manufacturing_rate=settings.manufacturing_rate,
            installation_rate=settings.installation_rate,
            design_rate=settings.design_rate,
            overhead_rate=settings.overhead_rate,
            profit_rate=settings.profit_rate,
            tech_director_rate=settings.tech_director_rate,
            manager_rate=settings.manager_rate,
            designer_rate=settings.designer_rate,
            measurement_fee=settings.measurement_fee,
            delivery_fee=settings.delivery_fee,
            delivery_fee_client=settings.delivery_fee_client,
            noncash_markup=settings.noncash_markup,
            min_margin_rate=getattr(settings, "min_margin_rate", 0.15),
            max_discount_percent=getattr(settings, "max_discount_percent", 10.0),
            sheet_reserve_rate=getattr(settings, "sheet_reserve_rate", 0.30),
            risk_reserve_rate=getattr(settings, "risk_reserve_rate", 0.0),
            version="1.0",
            designer_bonus_rate=settings.designer_bonus_rate,
            sheet_utilization=settings.sheet_utilization,
        )

    @classmethod
    def from_calc_settings(cls, row: Any) -> PricingPolicy:
        """Профиль из строки `CalcSettings`; None-поля берутся из config."""
        base = cls.from_config()
        values = {}
        for field_name in base.__dataclass_fields__:
            value = getattr(row, field_name, None)
            values[field_name] = base.__dict__[field_name] if value is None else float(value)
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        """Словарь для `calculate_cost` (совместимость со старым API)."""
        data = asdict(self)
        # calculate_cost ожидает delivery_fee/delivery_fee_client под этими именами.
        return data

    def as_cost_kwargs(self) -> dict[str, float]:
        """Только параметры, которые принимает `calculate_cost`."""
        return {
            "manufacturing_rate": self.manufacturing_rate,
            "installation_rate": self.installation_rate,
            "design_rate": self.design_rate,
            "overhead_rate": self.overhead_rate,
            "profit_rate": self.profit_rate,
            "tech_director_rate": self.tech_director_rate,
            "manager_rate": self.manager_rate,
            "designer_rate": self.designer_rate,
            "measurement_fee": self.measurement_fee,
            "delivery_fee": self.delivery_fee,
            "delivery_fee_client": self.delivery_fee_client,
            "noncash_markup": self.noncash_markup,
            "min_margin_rate": self.min_margin_rate,
        }

    @property
    def coefficients(self) -> dict[str, float]:
        """Только коэффициенты — для сверки версий и аудита."""
        return {
            "manufacturing_rate": self.manufacturing_rate,
            "installation_rate": self.installation_rate,
            "design_rate": self.design_rate,
            "overhead_rate": self.overhead_rate,
            "profit_rate": self.profit_rate,
            "tech_director_rate": self.tech_director_rate,
            "manager_rate": self.manager_rate,
            "designer_rate": self.designer_rate,
            "noncash_markup": self.noncash_markup,
            "min_margin_rate": self.min_margin_rate,
            "max_discount_percent": self.max_discount_percent,
            "sheet_reserve_rate": self.sheet_reserve_rate,
            "version": self.version,
            "sheet_utilization": self.sheet_utilization,
        }


def coerce_policy(value: Any | None) -> PricingPolicy:
    """Привести dict/PricingPolicy/None к PricingPolicy."""
    if isinstance(value, PricingPolicy):
        return value
    if isinstance(value, dict):
        base = PricingPolicy.from_config()
        merged = base.as_dict()
        for key, item in value.items():
            if key in merged and item is not None:
                merged[key] = float(item)
        return PricingPolicy(**merged)
    return PricingPolicy.from_config()


async def load_default_policy(session: AsyncSession) -> PricingPolicy:
    """Загрузить активный профиль по умолчанию; иначе — из config."""
    from app.models.settings import CalcSettings

    result = await session.execute(
        select(CalcSettings)
        .where(CalcSettings.is_active == 1)
        .order_by(CalcSettings.is_default.desc(), CalcSettings.id)
        .limit(1)
    )
    row = result.scalars().first()
    if row is None:
        return PricingPolicy.from_config()
    return PricingPolicy.from_calc_settings(row)
