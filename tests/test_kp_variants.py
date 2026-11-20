"""Варианты КП: три ступени, цены растут, скидка применяется."""

from __future__ import annotations

from app.services.kp_variants import build_variants

MODULES = [
    {
        "type": "lower_base",
        "width": 800,
        "depth": 560,
        "height": 820,
        "quantity": 2,
        "facades": {"count": 2},
        "drawers": {"count": 1},
    },
    {
        "type": "upper_base",
        "width": 800,
        "depth": 320,
        "height": 720,
        "quantity": 2,
        "facades": {"count": 2},
        "drawers": {"count": 0},
    },
]


def test_three_variants_in_order() -> None:
    variants = build_variants(MODULES, zone_type="Кухня")
    assert [variant.name for variant in variants] == ["Базовая", "Стандарт", "Комфорт"]


def test_prices_grow_from_base_to_comfort() -> None:
    variants = build_variants(MODULES, zone_type="Кухня")
    prices = [variant.final_cash for variant in variants]
    assert prices == sorted(prices)
    assert prices[-1] > prices[0]


def test_noncash_is_higher_than_cash() -> None:
    for variant in build_variants(MODULES, zone_type="Кухня"):
        assert variant.final_noncash > variant.final_cash


def test_discount_lowers_every_variant() -> None:
    full = build_variants(MODULES, zone_type="Кухня")
    discounted = build_variants(MODULES, zone_type="Кухня", discount_percent=10)
    for before, after in zip(full, discounted):
        assert after.final_cash < before.final_cash
        assert abs(after.final_cash - before.final_cash * 0.9) < 1


def test_variants_differ_by_materials() -> None:
    variants = build_variants(MODULES, zone_type="Кухня")
    assert len({variant.materials_text for variant in variants}) == len(variants)
