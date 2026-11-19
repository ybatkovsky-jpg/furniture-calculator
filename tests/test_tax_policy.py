"""День 4: TaxPolicy — УСН 15% от прибыли, НДС 0, минимальный налог 1%."""

from app.services.cost_calc import calculate_cost
from app.services.tax_policy import TaxPolicy


def test_default_tax_policy_is_usn_15_vat_0():
    policy = TaxPolicy.from_config()
    assert policy.regime == "УСН доходы минус расходы"
    assert policy.region == "Хабаровский край"
    assert policy.rate == 0.15
    assert policy.vat_rate == 0.0


def test_tax_from_profit_and_min_tax():
    policy = TaxPolicy.from_config()
    result = policy.calculate(revenue=142_653.06, deductible_expenses=100_000)

    assert result.tax_base == 42_653.06
    assert result.tax_usn == 6_397.96
    assert result.min_tax == 1_426.53
    assert result.tax_accrual == 6_397.96
    assert result.vat == 0
    assert result.net_profit == 36_255.10


def test_min_tax_used_when_profit_is_small():
    policy = TaxPolicy.from_config()
    result = policy.calculate(revenue=100_000, deductible_expenses=99_000)

    assert result.tax_usn == 150.0
    assert result.min_tax == 1_000.0
    assert result.tax_accrual == 1_000.0


def test_cost_breakdown_includes_tax_and_delivery_not_in_base():
    result = calculate_cost(
        material_cost=100_000,
        measurement_fee=0,
        delivery_fee=6500,
        delivery_fee_client=10_000,
    )

    # Доставка клиенту — отдельно, в базу и наценку не входит.
    assert result.delivery_client == 10_000
    assert result.delivery_internal == 6_500
    assert result.subtotal == (
        result.cost_price + result.manufacturing + result.installation
        + result.design + result.overhead
    )
    assert result.final_cash == result.total_base_cash + 10_000
    # Безнал: 10 % на мебель (110 из 130 строк КП), доставка без наценки.
    assert result.final_noncash == round(
        result.total_base_cash * 1.10 + 10_000, 2
    )
    # Налог — от прибыли, НДС 0, цена от налога не зависит.
    assert result.vat == 0
    assert result.tax_base == result.final_cash - result.cost_price - 6_500
    assert result.tax_accrual == max(result.tax_usn, result.min_tax)
