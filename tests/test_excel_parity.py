"""P0-02: Excel из снимка `quote` совпадает с чатом.

Второго расчёта в Excel больше нет: строки — из `quote["lines"]`, финансовая
секция — из `quote["totals"]`. Позиции, которых нет в шаблоне (стекло,
BLUM-петли), идут в «ПРОЧЕЕ» с ценой из снимка, а не молчаливым нулём.
"""

import asyncio
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Calculation, PriceItem, Project
from app.services.calculation_service import CalculationService
from app.services.template_filler import (
    _find_row_by_label,
    fill_template_from_quote,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"


async def _build_quote(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            PriceItem(name="ХДФ 3мм", category="ХДФ", unit_price=1530,
                      price_unit="лист", is_active=1),
            PriceItem(name="Конфирмат 7×50", category="Крепёж", unit_price=2,
                      price_unit="шт", is_active=1),
            PriceItem(name="Регулируемая опора", category="Крепёж", unit_price=50,
                      price_unit="шт", is_active=1),
            PriceItem(name="Настенный подвес", category="Крепёж", unit_price=120,
                      price_unit="шт", is_active=1),
            PriceItem(name="Цоколь ПВХ Rehau 4м", category="Крепёж", unit_price=1500,
                      price_unit="шт", is_active=1),
        ])
        project = Project(
            telegram_user_id="1", project_name="Тест", client_name="Клиент",
            client_phone="+70000000000",
        )
        session.add(project)
        await session.flush()
        calculation = Calculation(
            project_id=project.id,
            variant_name="Вариант 1",
            modules=[
                {
                    "type": "lower_base", "width": 600, "depth": 560, "height": 820,
                    "quantity": 1, "facades": {"count": 2, "type": "doors"},
                    "drawers": {"count": 2}, "shelves": 1,
                },
                {
                    "type": "lower_base", "width": 800, "depth": 400, "height": 900,
                    "quantity": 1, "facades": {"count": 2, "type": "glass"},
                    "has_glass": True,
                },
            ],
            selected_materials={
                "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
                "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                            "price_unit": "м²"},
                "edge": [
                    {"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34},
                    {"name": "Кромка 0.4", "thickness_mm": 0.4, "price": 17.5},
                ],
            },
            selected_hardware={
                "hinges": {"price": 300, "brand": "BLUM"},
                "drawers": {"name": "TANDEMBOX", "price": 3000},
                "handles": {"price": 500},
            },
            glass_items=[],
            zone_type="Гостиная",
        )
        session.add(calculation)
        await session.commit()

        service = CalculationService(session)
        await service.recalculate(calculation, reason="initial")
        await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        quote = dict(calculation.quote)
        await session.close()
    await engine.dispose()
    return quote


def _render(tmp_path):
    quote = asyncio.run(_build_quote(tmp_path))
    output = tmp_path / "smeta.xlsx"
    fill_template_from_quote(
        quote, str(TEMPLATE), str(output), sheet_name="Гостиная"
    )
    wb = load_workbook(output)
    return quote, wb


def test_excel_totals_match_quote(tmp_path):
    quote, wb = _render(tmp_path)
    ws = wb["Гостиная"]
    totals = quote["totals"]

    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    cash_row = _find_row_by_label(ws, "ИТОГО за наличку")
    assert cost_row and cash_row

    assert abs(float(ws.cell(cost_row, 5).value) - totals["cost_price"]) <= 0.05
    assert abs(float(ws.cell(cash_row, 5).value) - totals["final_cash"]) <= 1.0

    # Сумма строк себестоимости = СЕБЕСТОИМОСТЬ (ни одной строки «в обход»).
    def number(value):
        return float(value) if isinstance(value, (int, float)) else 0.0

    row_sum = sum(number(ws.cell(row, 6).value) for row in range(3, cost_row))
    assert abs(row_sum - totals["cost_price"]) <= 0.05


def test_no_silent_zero_rows(tmp_path):
    quote, wb = _render(tmp_path)
    ws = wb["Гостиная"]
    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    for row in range(3, cost_row):
        quantity = ws.cell(row, 5).value
        if isinstance(quantity, (int, float)) and quantity > 0:
            assert float(ws.cell(row, 6).value or 0) > 0, (
                f"R{row}: количество {quantity} без суммы"
            )


def test_facade_area_is_three_decimals(tmp_path):
    quote, wb = _render(tmp_path)
    ws = wb["Гостиная"]
    # Ищем строку ПВХ-фасада: 0,297 × 0,816 × 2 = 0,484704 → 0,485 (не 0,5).
    facade_row = None
    for row in range(3, _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")):
        text = str(ws.cell(row, 1).value or "").upper()
        if "ФАСАДЫ" in text and "ПВХ" in text and ws.cell(row, 5).value is not None:
            facade_row = row
            break
    assert facade_row is not None
    assert float(ws.cell(facade_row, 5).value) == 0.485


def test_positions_without_template_row_go_to_extra_block(tmp_path):
    quote, wb = _render(tmp_path)
    ws = wb["Гостиная"]
    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")

    glass_rows = [
        row for row in range(3, cost_row)
        if str(ws.cell(row, 1).value or "") == "Стекло (clear)"
    ]
    assert len(glass_rows) == 2  # два стекла — две строки, не одна

    blum_rows = [
        row for row in range(3, cost_row)
        if str(ws.cell(row, 1).value or "") == "Петли BLUM"
    ]
    assert len(blum_rows) == 1
    # Петли BLUM не записаны в строку FIRMAX.
    firmax_row = None
    for row in range(3, cost_row):
        if "FIRMAX" in str(ws.cell(row, 1).value or "").upper():
            firmax_row = row
            break
    assert firmax_row is None or not ws.cell(firmax_row, 5).value


def test_single_sheet_named_after_room(tmp_path):
    _quote, wb = _render(tmp_path)
    assert wb.sheetnames == ["Гостиная"]
