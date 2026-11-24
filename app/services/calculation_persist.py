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
        # P1-13/P2-23: почему КП заблокировано — явный список, а не «магия».
        "price_book": result.price_book,
        "policy": result.policy,
        "policy_version": str((result.policy or {}).get("version") or "1.0"),
        "recognition": {
            "confidence": calculation.recognition_confidence,
            "quality_score": calculation.quality_score,
        },
        "blocking_reasons": (
            []
            if result.can_issue_kp
            else (
                list(result.missing_prices)
                or list(result.warnings)
                or ["КП заблокировано: проверьте смету"]
            )
        ),
        "bom": [asdict(module_bom) for module_bom in result.bom],
        "nesting": asdict(result.nesting) if result.nesting else None,
        "parts_nesting": (
            asdict(result.parts_nesting) if result.parts_nesting else None
        ),
        "parts_area_m2": result.parts_area_m2,
        "parts_edge_totals": result.parts_edge_totals,
        "purchase": result.purchase.as_dict() if result.purchase else None,
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


def quote_from_result(
    result: CalculationResult,
    calculation: Calculation | None = None,
    *,
    reason: str = "preview",
) -> dict:
    """Собрать снимок `quote` из результата без сохранения в БД.

    Нужен документам (Excel/КП) и тестам: один и тот же формат снимка,
    что пишется в `Calculation.quote`.
    """
    target = calculation or Calculation()
    apply_result(target, result, reason=reason)
    return dict(target.quote or {})
