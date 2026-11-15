"""P2-18: спецификация применяется ко всем подходящим комнатам, но без дублей."""

from app.services.project_spec import ProjectSpec, SpecItem, apply_spec_to_quantities
from app.services.quantity_calc import MaterialQuantities


def test_spec_item_applied_once_across_rooms():
    spec = ProjectSpec(items=[SpecItem(name="Сушка для посуды", count=1, notes="кухня")])
    q1, q2 = MaterialQuantities(), MaterialQuantities()
    applied: set[str] = set()

    added1 = apply_spec_to_quantities(spec, q1, "Кухня", applied)
    added2 = apply_spec_to_quantities(spec, q2, "Кухня", applied)

    assert added1 == 1
    assert added2 == 0
    assert q1.drying_rack_count + q2.drying_rack_count == 1


def test_spec_item_waits_for_matching_room():
    spec = ProjectSpec(items=[SpecItem(name="Сушка для посуды", count=1, notes=" kitchen ")])
    applied: set[str] = set()
    bedroom = MaterialQuantities()
    kitchen = MaterialQuantities()

    assert apply_spec_to_quantities(spec, bedroom, "Спальня", applied) == 0
    assert apply_spec_to_quantities(spec, kitchen, "Кухня", applied) == 1
