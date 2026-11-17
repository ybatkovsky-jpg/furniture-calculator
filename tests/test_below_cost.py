"""P1-02: убыток определяется по полным затратам, доставка его не маскирует."""

from app.services.calc_engine import calculate_full_cost
from app.services.cost_calc import DiscountInfo, calculate_cost


def _cost(discount_percent=0.0, delivery_client=10000.0):
    return calculate_cost(
        material_cost=10000,
        discount=(
            DiscountInfo(discount_type="percent", discount_value=discount_percent)
            if discount_percent
            else None
        ),
        delivery_fee_client=delivery_client,
    )


def test_small_discount_is_not_below_cost():
    result = _cost(10)
    assert result.below_cost is False
    assert result.below_cost_gap == 0
    assert not any("ниже полных затрат" in w for w in result.warnings)


def test_large_discounts_are_below_cost():
    for percent in (45, 50, 70):
        result = _cost(percent)
        assert result.below_cost is True, percent
        assert result.below_cost_gap > 0
        assert any("ниже полных затрат" in w for w in result.warnings)


def test_delivery_does_not_mask_loss():
    """Вердикт одинаков при доставке 0 и 10 000: сравнение без доставки."""
    without = _cost(50, delivery_client=0)
    with_delivery = _cost(50, delivery_client=10000)
    assert without.below_cost is True
    assert with_delivery.below_cost is True
    assert abs(without.below_cost_gap - with_delivery.below_cost_gap) <= 0.01


def test_engine_blocks_kp_when_below_cost():
    module = {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 1, "facades": {"count": 1, "type": "doors"},
    }
    result = calculate_full_cost(
        [module],
        {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
         "facades": {"name": "ПВХ", "price": 10000, "price_unit": "м²"},
         "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
        {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}},
        [],
        discount_info=DiscountInfo(discount_type="percent", discount_value=70),
        zone_type="Гостиная",
        price_book={"hdf": 1530, "confirmat": 2, "adjustable_feet": 50},
    )
    assert result.cost_breakdown.below_cost is True
    assert result.can_issue_kp is False
    assert any("ниже полных затрат" in w for w in result.warnings)
