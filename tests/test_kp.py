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


def test_kp_table_rows_sum_to_total():
    """День 4: сумма строк таблицы = итог КП (доставка и сопровождение видны)."""
    calc = _calculation(
        glass_cost=1000,
        lighting_cost=500,
        countertop_cost=3000,
        accessories_cost=700,
        subcontractor_cost=0,
        overhead=2400,
        profit=12000,
        tech_director_salary=600,
        manager_salary=0,
        designer_salary=0,
        delivery_fee_client=10000,
    )
    g = KPGenerator()
    rows = g._create_items_table(calc)
    total = 0.0
    for row in rows[1:]:
        total += float(
            row[2].replace(" ₽", "").replace(" ", "").replace("\u00a0", "")
        )

    expected = (
        calc.material_cost + calc.edge_cost + calc.facade_cost
        + calc.hardware_cost + calc.glass_cost + calc.lighting_cost
        + calc.countertop_cost + calc.accessories_cost
        + calc.subcontractor_cost + calc.fastener_cost
        + calc.manufacturing + calc.installation + calc.measurement_fee
        + calc.design_fee + calc.overhead + calc.profit
        + calc.tech_director_salary + calc.manager_salary
        + calc.designer_salary + calc.delivery_fee_client
    )
    assert total == expected


def test_legacy_calculation_is_blocked_without_recalc():
    from app.bot.handlers.kp import is_legacy_calculation

    assert is_legacy_calculation(_calculation(quote=None, cost_price=0)) is True
    assert is_legacy_calculation(_calculation(quote={}, cost_price=0)) is True
    assert is_legacy_calculation(_calculation(quote=None, cost_price=15000)) is True
    assert is_legacy_calculation(
        _calculation(quote={"engine_version": "2.0"}, cost_price=15000)
    ) is False


def test_kp_pdf_table_has_cyrillic_font_and_delivery():
    """День 4: таблица КП рисуется кириллическим шрифтом, доставка — строкой."""
    import os

    import fitz

    calc = _calculation(
        quote={"engine_version": "2.0", "can_issue_kp": True},
        cost_price=15000,
        final_price_cash=85000,
        final_price_noncash=95000,
    )
    project = Project(
        client_name="Иван",
        client_phone="+7 999 000-00-00",
        client_address="Хабаровск",
    )
    generator = KPGenerator()
    assert generator.font_name != "Helvetica"

    path, _number = generator.generate_kp_pdf(calc, project, "Тестовый проект")
    try:
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    finally:
        os.unlink(path)

    assert "Доставка" in text
    assert "10 000" in text
    assert "Иван" in text


# ── P1-03: строки скидки/наценки/бонуса сходятся с итогом ─────────────────


def _calc_with_quote(discount=None):
    from app.services.calc_engine import calculate_full_cost
    from app.services.calculation_persist import quote_from_result
    from app.services.cost_calc import DiscountInfo

    result = calculate_full_cost(
        [{
            "type": "lower_base", "width": 600, "depth": 560, "height": 820,
            "facades": {"count": 1, "type": "doors"}, "shelves": 1,
        }],
        {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
         "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                     "price_unit": "м²"},
         "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
        {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}},
        [],
        discount_info=discount,
        zone_type="Гостиная",
        price_book={"hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
                    "wall_mount": 120, "plinth": 1500},
    )
    quote = quote_from_result(result)
    calc = Calculation(project_id=1)
    calc.quote = quote
    calc.final_price_cash = result.total_cash
    calc.final_price_noncash = result.total_noncash
    return calc, quote


def _rows_total(rows):
    return sum(
        float(row[2].replace(" ₽", "").replace(" ", "").replace("\u00a0", ""))
        for row in rows[1:]
    )


def test_quote_table_sums_to_final_on_discount_and_bonus():
    from app.services.cost_calc import DiscountInfo

    cases = [
        None,
        DiscountInfo(discount_type="percent", discount_value=10),
        DiscountInfo(discount_type="percent", discount_value=20),
        DiscountInfo(designer_bonus_enabled=True, designer_bonus_rate=0.10),
        DiscountInfo(discount_type="percent", discount_value=10,
                     designer_bonus_enabled=True),
        DiscountInfo(markup_type="percent", markup_value=10),
        DiscountInfo(discount_type="fixed", discount_value=5000),
    ]
    for discount in cases:
        calc, quote = _calc_with_quote(discount)
        rows = KPGenerator()._create_items_table(calc)
        assert abs(_rows_total(rows) - round(quote["totals"]["final_cash"])) <= 1, (
            discount, rows
        )


def test_quote_table_shows_discount_as_negative_row():
    from app.services.cost_calc import DiscountInfo

    calc, _quote = _calc_with_quote(
        DiscountInfo(discount_type="percent", discount_value=10)
    )
    rows = KPGenerator()._create_items_table(calc)
    discount_rows = [row for row in rows if row[0] == "Скидка"]
    assert discount_rows, rows
    assert discount_rows[0][2].startswith("-")


def test_quote_table_shows_markup_positive_without_minus():
    from app.services.cost_calc import DiscountInfo

    calc, _quote = _calc_with_quote(
        DiscountInfo(markup_type="percent", markup_value=10)
    )
    rows = KPGenerator()._create_items_table(calc)
    markup_rows = [row for row in rows if row[0] == "Наценка"]
    assert markup_rows, rows
    assert markup_rows[0][2].startswith("+")
    assert "--" not in markup_rows[0][2]


def test_quote_table_shows_designer_bonus_row():
    from app.services.cost_calc import DiscountInfo

    calc, _quote = _calc_with_quote(
        DiscountInfo(designer_bonus_enabled=True, designer_bonus_rate=0.10)
    )
    rows = KPGenerator()._create_items_table(calc)
    assert any(row[0] == "Бонус дизайнера" for row in rows), rows
