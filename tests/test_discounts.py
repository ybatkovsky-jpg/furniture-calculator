"""Тесты скидок/наценок/бонуса и их валидации (P0-08)."""

import pytest

from app.domain.validation import PricingError
from app.services.cost_calc import DiscountInfo, calculate_cost


def _base(**kwargs):
    return calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        **kwargs,
    )


def test_no_discount():
    """Test calculation without any discount."""
    result = _base(discount=DiscountInfo())

    # Price after discount should equal total_base_cash when no discount
    assert result.price_after_discount == result.total_base_cash
    assert result.discount_amount == 0


def test_percent_discount():
    """Test percent discount calculation."""
    result = _base(discount=DiscountInfo(discount_type="percent", discount_value=10))

    # Discount should be applied
    assert result.discount_amount > 0
    assert result.price_after_discount < result.total_base_cash


def test_fixed_discount():
    """Test fixed amount discount."""
    discount_fixed = 5000

    result = _base(
        discount=DiscountInfo(discount_type="fixed", discount_value=discount_fixed)
    )

    assert result.discount_amount == discount_fixed
    assert result.price_after_discount == result.total_base_cash - discount_fixed


def test_percent_markup():
    """Test percent markup (increase price)."""
    result = _base(discount=DiscountInfo(markup_type="percent", markup_value=15))

    # Markup should increase price
    assert result.discount_amount < 0  # Negative for markup
    assert result.price_after_discount > result.total_base_cash


def test_designer_bonus():
    """Test designer bonus calculation."""
    result = _base(
        discount=DiscountInfo(designer_bonus_enabled=True, designer_bonus_rate=0.10)
    )

    # Final cash should be higher than price_after_discount
    assert result.final_cash > result.price_after_discount
    assert result.designer_bonus_amount > 0


# ── P0-08: границы ──────────────────────────────────────────────────────────


def test_huge_fixed_discount_rejected():
    """Скидка 1 000 000 ₽ при базе ~1 500 ₽ раньше давала −983 216 ₽."""
    with pytest.raises(PricingError, match="больше суммы"):
        calculate_cost(
            material_cost=1000,
            discount=DiscountInfo(discount_type="fixed", discount_value=1_000_000),
        )


def test_percent_over_100_rejected():
    with pytest.raises(PricingError, match="100%"):
        _base(discount=DiscountInfo(discount_type="percent", discount_value=150))


def test_bonus_rate_one_does_not_divide_by_zero():
    with pytest.raises(PricingError, match="бонуса"):
        _base(
            discount=DiscountInfo(
                designer_bonus_enabled=True, designer_bonus_rate=1.0
            )
        )


def test_discount_and_markup_together_rejected():
    with pytest.raises(PricingError, match="одновременно"):
        _base(
            discount=DiscountInfo(
                discount_type="percent",
                discount_value=5,
                markup_type="percent",
                markup_value=5,
            )
        )


def test_negative_component_rejected():
    with pytest.raises(PricingError, match="отрицательным"):
        calculate_cost(material_cost=-100)


def test_discount_equal_to_base_is_allowed():
    result = _base(discount=DiscountInfo())
    base = result.total_base_cash
    ok = _base(discount=DiscountInfo(discount_type="fixed", discount_value=base))
    # Мебель обнулилась скидкой, но доставка клиенту (10 000 ₽) остаётся
    # отдельной строкой и не входит в базу скидки.
    assert ok.final_cash == ok.delivery_client


def test_below_cost_price_warns_but_calculates():
    """P1-02: ниже ПОЛНЫХ затрат — предупреждение (КП блокируется), не падение.

    Раньше сравнивали final_cash (с доставкой) с материалами; теперь —
    цену мебели без доставки с subtotal, поэтому скидка 95 % попадает в флаг
    below_cost с явным разрывом.
    """
    result = _base(discount=DiscountInfo(discount_type="percent", discount_value=95))
    assert result.final_cash >= 0
    assert result.below_cost is True
    assert result.below_cost_gap > 0
    assert result.warnings
    assert "ниже полных затрат" in result.warnings[0]
