"""
Tests for discount functionality in cost_calc.py
"""

import pytest
from app.services.cost_calc import calculate_cost, DiscountInfo


def test_no_discount():
    """Test calculation without any discount."""
    result = calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        discount=DiscountInfo()
    )

    # Price after discount should equal total_base_cash when no discount
    assert result.price_after_discount == result.total_base_cash
    assert result.discount_amount == 0


def test_percent_discount():
    """Test percent discount calculation."""
    result = calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        discount=DiscountInfo(discount_type="percent", discount_value=10)
    )

    # Discount should be applied
    assert result.discount_amount > 0
    assert result.price_after_discount < result.total_base_cash


def test_fixed_discount():
    """Test fixed amount discount."""
    discount_fixed = 5000

    result = calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        discount=DiscountInfo(discount_type="fixed", discount_value=discount_fixed)
    )

    assert result.discount_amount == discount_fixed
    assert result.price_after_discount == result.total_base_cash - discount_fixed


def test_percent_markup():
    """Test percent markup (increase price)."""
    result = calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        discount=DiscountInfo(markup_type="percent", markup_value=15)
    )

    # Markup should increase price
    assert result.discount_amount < 0  # Negative for markup
    assert result.price_after_discount > result.total_base_cash


def test_designer_bonus():
    """Test designer bonus calculation."""
    result = calculate_cost(
        material_cost=10000,
        edge_cost=1000,
        facade_cost=5000,
        hardware_cost=2000,
        discount=DiscountInfo(designer_bonus_enabled=True, designer_bonus_rate=0.10)
    )

    # Final cash should be higher than price_after_discount
    assert result.final_cash > result.price_after_discount
    assert result.designer_bonus_amount > 0