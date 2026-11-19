"""Налоговая политика заказа (День 4).

Подтверждено: УСН «доходы минус расходы», 15%, Хабаровский край; НДС не
применяется (v = 0). Налог УСН считается от прибыли, а не от выручки, поэтому
в цену клиента он не «встраивается» отдельной строкой и не меняет её.

Здесь считается только управленческий результат заказа: налоговая база,
налог 15%, минимальный налог 1% и чистая прибыль. Цена клиенту
(`CostBreakdown.final_cash`) формируется до налога.
"""

from __future__ import annotationsfrom dataclasses import dataclassfrom app.config import settingsfrom app.domain.money import money_float@dataclass(frozen=True)
class TaxResult:
    """Результат налогового расчёта по заказу."""

    regime: str
    region: str
    revenue: float
    expenses: float
    tax_base: float
    tax_usn: float
    min_tax: float
    tax_accrual: float
    vat: float
    net_profit: float


@dataclass(frozen=True)
class TaxPolicy:
    """Параметры УСН 15% / НДС 0 для ООО «ПРО Мебель»."""

    regime: str = "УСН доходы минус расходы"
    region: str = "Хабаровский край"
    rate: float = 0.15
    vat_rate: float = 0.0
    min_tax_rate: float = 0.01

    @classmethod
    def from_config(cls) -> TaxPolicy:
        return cls(
            regime=settings.tax_regime,
            region=settings.tax_region,
            rate=settings.tax_rate,
            vat_rate=settings.vat_rate,
            min_tax_rate=settings.min_tax_rate,
        )

    def calculate(self, revenue: float, deductible_expenses: float) -> TaxResult:
        """Налог УСН от прибыли; минимальный налог — 1% с доходов.

        `revenue` — выручка по заказу (цены клиенту), `deductible_expenses` —
        подтверждённые расходы. Для заказа берётся консервативная оценка
        `max(Tax_15, Min_tax)`, как в `docs/financial_policy_v1.0.md` § 5.4.
        """
        revenue = float(revenue or 0)
        expenses = float(deductible_expenses or 0)
        tax_base = revenue - expenses

        tax_usn = money_float(max(0.0, tax_base) * self.rate)
        min_tax = money_float(revenue * self.min_tax_rate)
        tax_accrual = max(tax_usn, min_tax)
        vat = money_float(revenue * self.vat_rate)
        net_profit = money_float(tax_base - tax_accrual)

        return TaxResult(
            regime=self.regime,
            region=self.region,
            revenue=money_float(revenue),
            expenses=money_float(expenses),
            tax_base=money_float(tax_base),
            tax_usn=tax_usn,
            min_tax=min_tax,
            tax_accrual=tax_accrual,
            vat=vat,
            net_profit=net_profit,
        )

