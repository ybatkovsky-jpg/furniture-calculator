"""P1-04: петли и ручки по типам модулей и по способу открывания.

До исправления:
- типы cabinet/wardrobe/tall_cabinet/vanity/drawer_unit не получали петель
  и ручек даже при дверях (0/0 вместо 8/4 на пенале);
- в «Спальне»/«Гардеробной» зона отключала петли у ВСЕХ модулей;
- модуль «2 двери + 3 ящика» получал 2 ручки вместо 5 (elif).
"""

import pytest

from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import RecognizedModule
from app.services.quantity_calc import calculate_quantities

HINGED_TYPES = [
    "lower_base", "upper_base", "penal", "column", "tumbler",
    "cabinet", "wardrobe", "tall_cabinet", "vanity", "drawer_unit",
]


@pytest.mark.parametrize("module_type", HINGED_TYPES)
def test_all_hinged_types_get_hinges_and_handles(module_type):
    quantities = calculate_quantities(
        [RecognizedModule(
            type=module_type, width=600, depth=600, height=2100,
            facades={"count": 2, "type": "doors"},
        )],
        "", [], zone_type="Гостиная",
    )
    assert quantities.hinges_count > 0, module_type
    assert quantities.handles_count > 0, module_type


def test_bedroom_explicit_hinged_matches_living_room():
    bedroom = calculate_quantities(
        [RecognizedModule(
            type="penal", width=600, depth=600, height=2100,
            facades={"count": 2, "type": "doors"}, door_system="hinged",
        )],
        "", [], zone_type="Спальня",
    )
    living = calculate_quantities(
        [RecognizedModule(
            type="penal", width=600, depth=600, height=2100,
            facades={"count": 2, "type": "doors"},
        )],
        "", [], zone_type="Гостиная",
    )
    assert bedroom.hinges_count == living.hinges_count > 0
    assert bedroom.handles_count == living.handles_count > 0


def test_sliding_module_has_no_hinges_or_overlay_handles():
    quantities = calculate_quantities(
        [RecognizedModule(
            type="wardrobe", width=1600, depth=600, height=2400,
            facades={"count": 2, "type": "doors"}, door_system="sliding",
        )],
        "", [], zone_type="Гардеробная",
    )
    assert quantities.hinges_count == 0
    assert quantities.handles_count == 0


def test_doors_plus_drawers_get_five_handles():
    quantities = calculate_quantities(
        [RecognizedModule(
            type="lower_base", width=900, depth=560, height=820,
            facades={"count": 2, "type": "doors"}, drawers={"count": 3},
        )],
        "", [], zone_type="Кухня",
    )
    # 2 двери + 3 ящика = 5 ручек (было 2 из-за elif).
    assert quantities.handles_count == 5


def test_glass_doors_do_not_double_hinges():
    glass = calculate_quantities(
        [RecognizedModule(
            type="cabinet", width=800, depth=400, height=900,
            facades={"count": 2, "type": "glass"}, has_glass=True,
        )],
        "", [], zone_type="Гостиная",
    )
    normal = calculate_quantities(
        [RecognizedModule(
            type="cabinet", width=800, depth=400, height=900,
            facades={"count": 2, "type": "doors"},
        )],
        "", [], zone_type="Гостиная",
    )
    assert glass.hinges_count == normal.hinges_count > 0


def test_full_cost_uses_door_system_from_module():
    module = {
        "type": "cabinet", "width": 600, "depth": 600, "height": 2100,
        "quantity": 1, "facades": {"count": 2, "type": "doors"},
        "door_system": "hinged",
    }
    result = calculate_full_cost(
        [module],
        {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
         "facades": {"name": "ПВХ", "price": 10000, "price_unit": "м²"},
         "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
        {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}},
        [], zone_type="Спальня",
    )
    assert result.hardware_calc.hinges_total > 0
    assert result.hardware_calc.handles_total > 0
