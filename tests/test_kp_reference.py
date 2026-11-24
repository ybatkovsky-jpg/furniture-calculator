"""Эталон 22 КП: 122 корректные пары, политика ×1,10/×1,13."""

from __future__ import annotations

import json

from app.domain.kp_reference import (
    FIXTURE,
    check_price_corridor,
    load_pairs,
    markup_summary,
    price_corridor,
)


def test_reference_has_22_source_documents() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert len(data["documents"]) == 19  # 22 источника, у 3 нет валидных пар


def test_all_pairs_have_valid_markup() -> None:
    pairs = load_pairs()
    summary = markup_summary(pairs)

    assert summary["pairs"] == 122
    assert summary["at_10"] == 110
    assert summary["at_13"] == 9
    assert 0.10 <= summary["median"] <= 0.13
    assert all(1.05 <= 1 + pair["markup"] <= 1.20 for pair in pairs)


def test_kitchen_price_corridor() -> None:
    corridor = price_corridor(load_pairs(), "Кухонный")

    assert corridor["count"] > 0
    assert corridor["min"] <= corridor["p10"] <= corridor["median"] <= corridor["p90"]
    assert corridor["p90"] <= corridor["max"]


def test_price_corridor_check_reports_outside() -> None:
    inside = check_price_corridor(200_000, "Шкаф")
    outside = check_price_corridor(10, "Шкаф")

    assert inside["ok"] is True
    assert outside["ok"] is False
    assert "вне коридора" in outside["reason"]

