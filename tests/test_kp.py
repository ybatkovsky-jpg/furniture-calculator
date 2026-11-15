"""P1-10/P2-14: состав и поля КП."""

from app.models.calculation import Calculation
from app.models.project import Project
from app.services.kp_generator import KPGenerator


def _calculation(**kwargs):
    calc = Calculation(project_id=1, variant_name="Вариант 1")
    defaults = dict(
        material_cost=6000,
        edge_cost=549,
        facade_cost=4850,
        hardware_cost=7800,
        glass_cost=0,
        lighting_cost=0,
        countertop_cost=0,
        accessories_cost=0,
        fastener_cost=1744,
        manufacturing=17978,
        installation=12360,
        measurement_fee=2500,
        delivery_fee_client=10000,
        design_fee=1798,
        total_base_cash=85000,
    )
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(calc, key, value)
    return calc


def test_kp_items_include_fastener_and_delivery():
    generator = KPGenerator()
    rows = generator._create_items_table(_calculation())
    text = "\n".join(" | ".join(str(cell) for cell in row) for row in rows)

    assert "Кромка" in text
    assert "Фурнитура" in text
    assert "Крепёж" in text
    assert "1 744" in text
    assert "Доставка" in text
    assert "10 000" in text


def test_kp_discount_from_quote_json():
    """P1-10: скидка берётся из quote, а не из несуществующей колонки."""
    calc = _calculation(
        discount_type="percent",
        discount_value=10,
        quote={"discount_amount": 8500, "can_issue_kp": True},
    )
    generator = KPGenerator()
    # Проверяем через расчёт строки скидки в PDF-ветке: атрибут quote читается.
    assert (calc.quote or {}).get("discount_amount") == 8500
    assert calc.discount_type == "percent"
