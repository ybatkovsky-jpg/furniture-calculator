"""Golden-набор КП: эталонный состав → цена движка.

Фиксирует расчёт по ручной разметке 8 позиций «волонтёрского центра».
Тест ловит регрессии движка (цены, коэффициенты, раскрой): если эталонная
цена изменилась — значит поменялся расчёт, и это надо объяснить.

Состав размечен по рендеру и подлежит подтверждению техдиром; сами цены КП
здесь только для отчёта, тест их не сравнивает.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.calc_engine import calculate_full_cost

FIXTURE = Path(__file__).parent / "fixtures" / "kp_volunteer_golden.json"

MATS = {
    "ldsp": {"name": "ТОМЛЕСДРЕВ белый", "price": 3600, "price_unit": "лист",
             "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "facades": {"name": "ЛДСП Томлесдрев", "price": 3600, "price_unit": "лист",
                "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 200, "brand": "FIRMAX"},
      "drawers": {"price": 3300}, "handles": {"price": 500}}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500,
      "edge_0.4": 22, "countertop": 2834}


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("index", range(8))
def test_golden_item_price_is_stable(index: int) -> None:
    item = _fixture()["items"][index]
    # Эталон зафиксирован при нулевом запасе на ошибки: тест не должен
    # зависеть от RISK_RESERVE_RATE в .env.
    result = calculate_full_cost(
        item["modules"], MATS, HW, [], zone_type=item["zone"], price_book=PB,
        settings={"risk_reserve_rate": 0.0},
    )
    our_total = round(result.cost_breakdown.furniture_cash) * item["qty"]
    assert our_total == item["our_total"], (
        f"{item['title']}: было {item['our_total']}, стало {our_total}"
    )


def test_golden_covers_all_kp_items() -> None:
    items = _fixture()["items"]
    assert len(items) == 8
    assert [item["qty"] for item in items] == [1, 1, 1, 8, 5, 2, 1, 6]
    assert sum(item["kp_total"] for item in items) == 1_704_600
