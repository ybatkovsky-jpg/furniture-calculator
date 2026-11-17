"""Сохранение результата расчёта в Calculation (единая точка, P1-10)."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import UTC, datetime

from app.models.calculation import Calculation
from app.services.calc_engine import CalculationResult


def apply_result(
    calculation: Calculation,
    result: CalculationResult,
    *,
    reason: str = "manual",
) -> None:
    """Записать в расчёт весь CostBreakdown, итоги и снимок quote.

    `reason` — почему пересчитано (`initial`, `discount`, `designer_bonus`,
    `price_update`…). Попадает в снимок: по нему потом видно, откуда взялась
    сумма, без чтения логов (P1-10).
    """
    breakdown = result.cost_breakdown
    calculation.material_cost = result.material_cost
    calculation.edge_cost = result.edge_cost
    calculation.facade_cost = result.facade_cost
    calculation.hardware_cost = result.hardware_cost
    calculation.glass_cost = result.glass_cost
    calculation.lighting_cost = result.lighting_cost
    calculation.countertop_cost = result.countertop_cost
    calculation.accessories_cost = result.accessories_cost
    calculation.subcontractor_cost = result.subcontractor_cost
    calculation.fastener_cost = result.fastener_cost
    calculation.cost_price = breakdown.cost_price
    calculation.manufacturing = breakdown.manufacturing
    calculation.installation = breakdown.installation
    calculation.measurement_fee = breakdown.measurement
    calculation.delivery_fee = breakdown.delivery_internal
    calculation.delivery_fee_client = breakdown.delivery_client
    calculation.design_fee = breakdown.design
    calculation.overhead = breakdown.overhead
    calculation.profit = breakdown.profit
    calculation.tech_director_salary = breakdown.tech_director
    calculation.manager_salary = breakdown.manager_salary
    calculation.designer_salary = breakdown.designer_salary
    calculation.total_base_cash = breakdown.total_base_cash
    calculation.price_after_discount = breakdown.price_after_discount
    calculation.designer_bonus_amount = breakdown.designer_bonus_amount
    calculation.final_price_cash = result.total_cash
    calculation.final_price_noncash = result.total_noncash
    calculation.quote = {
        "engine_version": "2.0",
        "quote_version": "2.1",
        "created_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "zone_type": calculation.zone_type,
        "lines": [asdict(line) for line in result.lines],
        "warnings": result.warnings,
        "missing_prices": result.missing_prices,
        "can_issue_kp": result.can_issue_kp,
        "bom": [asdict(module_bom) for module_bom in result.bom],
        "nesting": asdict(result.nesting) if result.nesting else None,
        "cost_price": breakdown.cost_price,
        "discount_amount": breakdown.discount_amount,
        "total_base_cash": breakdown.total_base_cash,
        "furniture_cash": breakdown.furniture_cash,
        "below_cost": breakdown.below_cost,
        "below_cost_gap": breakdown.below_cost_gap,
        "delivery_client": breakdown.delivery_client,
        "delivery_internal": breakdown.delivery_internal,
        "tax": {
            "regime": "УСН доходы минус расходы",
            "region": "Хабаровский край",
            "tax_base": breakdown.tax_base,
            "tax_usn": breakdown.tax_usn,
            "min_tax": breakdown.min_tax,
            "tax_accrual": breakdown.tax_accrual,
            "vat": breakdown.vat,
            "net_profit": breakdown.net_profit,
        },
        "total_cash": result.total_cash,
        "total_noncash": result.total_noncash,
        # P0-02/P1-10: полная финансовая разбивка в снимке. Документы
        # (Excel/КП) берут итоги отсюда, а не пересчитывают по ячейкам.
        "totals": {
            item.name: getattr(breakdown, item.name)
            for item in fields(breakdown)
            if item.name != "warnings"
        },
    }
