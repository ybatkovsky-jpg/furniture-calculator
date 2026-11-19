"""РќР°Р»РѕРіРѕРІР°СЏ РїРѕР»РёС‚РёРєР° Р·Р°РєР°Р·Р° (Р”РµРЅСЊ 4).

РџРѕРґС‚РІРµСЂР¶РґРµРЅРѕ: РЈРЎРќ В«РґРѕС…РѕРґС‹ РјРёРЅСѓСЃ СЂР°СЃС…РѕРґС‹В», 15%, РҐР°Р±Р°СЂРѕРІСЃРєРёР№ РєСЂР°Р№; РќР”РЎ РЅРµ
РїСЂРёРјРµРЅСЏРµС‚СЃСЏ (v = 0). РќР°Р»РѕРі РЈРЎРќ СЃС‡РёС‚Р°РµС‚СЃСЏ РѕС‚ РїСЂРёР±С‹Р»Рё, Р° РЅРµ РѕС‚ РІС‹СЂСѓС‡РєРё, РїРѕСЌС‚РѕРјСѓ
РІ С†РµРЅСѓ РєР»РёРµРЅС‚Р° РѕРЅ РЅРµ В«РІСЃС‚СЂР°РёРІР°РµС‚СЃСЏВ» РѕС‚РґРµР»СЊРЅРѕР№ СЃС‚СЂРѕРєРѕР№ Рё РЅРµ РјРµРЅСЏРµС‚ РµС‘.

Р—РґРµСЃСЊ СЃС‡РёС‚Р°РµС‚СЃСЏ С‚РѕР»СЊРєРѕ СѓРїСЂР°РІР»РµРЅС‡РµСЃРєРёР№ СЂРµР·СѓР»СЊС‚Р°С‚ Р·Р°РєР°Р·Р°: РЅР°Р»РѕРіРѕРІР°СЏ Р±Р°Р·Р°,
РЅР°Р»РѕРі 15%, РјРёРЅРёРјР°Р»СЊРЅС‹Р№ РЅР°Р»РѕРі 1% Рё С‡РёСЃС‚Р°СЏ РїСЂРёР±С‹Р»СЊ. Р¦РµРЅР° РєР»РёРµРЅС‚Сѓ
(`CostBreakdown.final_cash`) С„РѕСЂРјРёСЂСѓРµС‚СЃСЏ РґРѕ РЅР°Р»РѕРіР°.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.domain.money import money_float


@dataclass(frozen=True)
class TaxResult:
    """Р РµР·СѓР»СЊС‚Р°С‚ РЅР°Р»РѕРіРѕРІРѕРіРѕ СЂР°СЃС‡С‘С‚Р° РїРѕ Р·Р°РєР°Р·Сѓ."""

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
    """РџР°СЂР°РјРµС‚СЂС‹ РЈРЎРќ 15% / РќР”РЎ 0 РґР»СЏ РћРћРћ В«РџР Рћ РњРµР±РµР»СЊВ»."""

    regime: str = "РЈРЎРќ РґРѕС…РѕРґС‹ РјРёРЅСѓСЃ СЂР°СЃС…РѕРґС‹"
    region: str = "РҐР°Р±Р°СЂРѕРІСЃРєРёР№ РєСЂР°Р№"
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
        """РќР°Р»РѕРі РЈРЎРќ РѕС‚ РїСЂРёР±С‹Р»Рё; РјРёРЅРёРјР°Р»СЊРЅС‹Р№ РЅР°Р»РѕРі вЂ” 1% СЃ РґРѕС…РѕРґРѕРІ.

        `revenue` вЂ” РІС‹СЂСѓС‡РєР° РїРѕ Р·Р°РєР°Р·Сѓ (С†РµРЅС‹ РєР»РёРµРЅС‚Сѓ), `deductible_expenses` вЂ”
        РїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅС‹Рµ СЂР°СЃС…РѕРґС‹. Р”Р»СЏ Р·Р°РєР°Р·Р° Р±РµСЂС‘С‚СЃСЏ РєРѕРЅСЃРµСЂРІР°С‚РёРІРЅР°СЏ РѕС†РµРЅРєР°
        `max(Tax_15, Min_tax)`, РєР°Рє РІ `docs/financial_policy_v1.0.md` В§ 5.4.
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

