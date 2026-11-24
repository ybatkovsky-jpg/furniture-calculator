"""Крепёж в КП: только позиции, которые есть в прайсе/Excel.

Присадка и точная фурнитура после договора не считаются. Из крепежа
берём только те позиции, что есть в прайсе: конфирмат, опоры, подвесы.
Саморезы/гвозди не выдумываем, если их нет в прайсе.
"""

from __future__ import annotations

from app.services.image_analyzer import RecognizedModule
from app.services.quantity_calc import calculate_quantities


def test_only_price_list_fasteners_are_counted() -> None:
    module = RecognizedModule(
        type="lower_base",
        width=800,
        depth=560,
        height=820,
        quantity=1,
        shelves=1,
        facades={"count": 2},
    )
    quantities = calculate_quantities([module], room_name="Кухня")

    assert quantities.confirmat_count > 0
    assert quantities.adjustable_feet > 0
    assert quantities.wall_mounts >= 0
