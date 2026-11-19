"""Тест разбора КП на товарные блоки (пилот «волонтёрский центр»).

Фиксирует то, что уже проверено руками: 8 позиций, цена каждой и количество.
Тест ловит регрессии измерителя (привязка цены, «N шт», сегментация), а не
качество VLM.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PILOT = Path(__file__).parents[1] / "scripts" / "kp_blocks_pilot.py"
KP = Path(__file__).parents[1] / "KP" / "Коммерческое предложение волонтерский центр.pdf"


@pytest.fixture(scope="module")
def pilot():
    spec = importlib.util.spec_from_file_location("kp_blocks_pilot", PILOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not KP.exists(), reason="нет PDF КП в KP/")
def test_volunteer_center_blocks(pilot):
    import pymupdf

    doc = pymupdf.open(KP)
    items = []
    for page in doc:
        items.extend(pilot._items(page))

    assert len(items) == 8, [item["title"] for item in items]

    expected = [
        (375_000, 1),
        (159_000, 1),
        (149_000, 1),
        (45_700, 8),
        (54_000, 5),
        (109_000, 2),
        (46_200, 1),
        (20_300, 6),
    ]
    assert [(item["price"], item["qty"]) for item in items] == expected

    # Габариты тоже из текста, а не «стандартные 600».
    assert items[0]["width"] == 3000 and items[0]["height"] == 2550
    assert items[3]["width"] == 1800 and items[3]["height"] == 1100
