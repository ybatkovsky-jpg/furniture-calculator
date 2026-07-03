"""
Генератор Excel в формате шаблона с рассчитанными количествами.

На вход: PipelineResult (AI-модули)
На выход: Excel с вкладками по помещениям в формате:
  Наименование | Цвет | Поставщик | С/С | Количество | Планируемая С/С
"""

import logging
from pathlib import Path
from typing import List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter

from app.services.full_pipeline import PipelineResult
from app.services.quantity_calc import fill_template_for_room, MaterialQuantities, calculate_quantities

logger = logging.getLogger(__name__)

# Стили
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
SECTION_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
SECTION_FONT = Font(name="Arial", size=11, bold=True)
NORMAL_FONT = Font(name="Arial", size=9)
TOTAL_FONT = Font(name="Arial", size=11, bold=True)
TOTAL_FILL = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)

# Коэффициенты из прайса
COEFFS = {
    "manufacturing": 0.80,
    "installation": 0.55,
    "design": 0.10,
    "overhead": 0.05,
    "profit": 0.30,
    "tech_director": 0.015,
    "measurement": 2500,
    "delivery": 10000,
    "noncash": 1.13,
}


def generate_calculation_excel(
    pipeline_result: PipelineResult,
    output_path: str = "Расчет_по_модулям.xlsx",
    template_path: str = None,
) -> str:
    """
    Сгенерировать Excel с расчётом для каждого помещения.

    Если передан template_path — заполняет существующий шаблон (колонка «Количество»).
    Иначе — генерирует новый Excel с нуля в упрощённом формате.
    """
    # Режим заполнения шаблона
    if template_path:
        from app.services.template_filler import fill_template_from_pipeline
        logger.info(f"📄 Режим шаблона: {template_path}")
        return fill_template_from_pipeline(
            pipeline_result=pipeline_result,
            template_path=template_path,
            output_path=output_path,
        )

    # Режим генерации с нуля
    wb = Workbook()
    wb.remove(wb.active)

    # ── Вкладки по помещениям с модулями ──
    for room in pipeline_result.rooms:
        if not room.modules:
            continue

        sheet_name = _safe_name(room.room_name)
        ws = wb.create_sheet(title=sheet_name)
        _write_calculation_sheet(ws, room, pipeline_result)

    # ── Сводка ──
    ws_sum = wb.create_sheet(title="СВОДКА")
    _write_summary(ws_sum, pipeline_result)

    wb.save(output_path)
    logger.info(f"✅ Расчёт сохранён: {output_path}")
    return output_path


def _safe_name(name: str) -> str:
    for ch in r'[]:*?/\\':
        name = name.replace(ch, "")
    return name.strip()[:31] or "Лист"


def _write_calculation_sheet(ws, room, result: PipelineResult):
    """Заполнить лист расчёта для одного помещения."""
    items = fill_template_for_room(room.modules, room.room_name, room.materials)

    # Заголовок
    ws.merge_cells("A1:F1")
    ws["A1"].value = f"РАСЧЁТ: {room.room_name}"
    ws["A1"].font = Font(name="Arial", size=13, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")

    if result.project_address:
        ws.merge_cells("A2:F2")
        ws["A2"].value = result.project_address
        ws["A2"].font = Font(name="Arial", size=9, color="666666")

    # Шапка таблицы
    row = 4
    headers = ["Наименование", "Цвет / Поставщик", "Примечание",
               "С/С, ₽", "Количество", "Планируемая С/С, ₽"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = THIN_BORDER

    # Группируем по категориям
    current_category = ""
    row = 5
    total_cost = 0

    for cat, name, note, price, qty in items:
        # Заголовок категории
        if cat != current_category:
            current_category = cat
            ws.merge_cells(f"A{row}:F{row}")
            cell = ws.cell(row=row, column=1, value=cat)
            cell.font = SECTION_FONT
            cell.fill = SECTION_FILL
            cell.border = THIN_BORDER
            row += 1

        # Строка позиции
        cost = price * qty if price and qty else 0
        total_cost += cost

        data = [
            name,
            note if note else "",
            "",
            price if price else "",
            qty if qty else "",
            round(cost, 2) if cost else "",
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = NORMAL_FONT
            cell.border = THIN_BORDER
            if col == 4:
                cell.number_format = '#,##0'
            elif col == 6:
                cell.number_format = '#,##0'
        row += 1

    # ── Финансовый расчёт ──
    row += 1
    ws.merge_cells(f"A{row}:F{row}")
    ws.cell(row=row, column=1, value="РАСЧЁТ СТОИМОСТИ").font = Font(name="Arial", size=12, bold=True)
    row += 1

    calcs = [
        ("СЕБЕСТОИМОСТЬ материалов", total_cost),
        ("ИЗГОТОВЛЕНИЕ", round(total_cost * COEFFS["manufacturing"])),
        ("МОНТАЖ", round(total_cost * COEFFS["installation"])),
        ("ЗАМЕР", COEFFS["measurement"]),
        ("ДОСТАВКА", COEFFS["delivery"]),
        ("ПРОЕКТИРОВКА", round(total_cost * COEFFS["manufacturing"] * COEFFS["design"])),
        ("ПРОЧИЕ РАСХОДЫ", round(total_cost * COEFFS["overhead"])),
    ]

    subtotal = 0
    for label, amount in calcs:
        ws.cell(row=row, column=1, value="").border = THIN_BORDER
        ws.cell(row=row, column=2, value="").border = THIN_BORDER
        ws.cell(row=row, column=3, value=label).font = NORMAL_FONT
        ws.cell(row=row, column=3).border = THIN_BORDER
        ws.cell(row=row, column=4, value=amount).font = NORMAL_FONT
        ws.cell(row=row, column=4).number_format = '#,##0'
        ws.cell(row=row, column=4).border = THIN_BORDER
        ws.cell(row=row, column=5, value="").border = THIN_BORDER
        ws.cell(row=row, column=6, value="").border = THIN_BORDER
        subtotal += amount
        row += 1

    # Итого
    ws.merge_cells(f"A{row}:B{row}")
    profit = round(subtotal * COEFFS["profit"])
    total_with_profit = subtotal + profit
    tech_dir = round(total_with_profit * COEFFS["tech_director"])
    cash = total_with_profit + tech_dir

    totals = [
        ("ИТОГО:", subtotal),
        ("ПЛАНИРУЕМАЯ ПРИБЫЛЬ", profit),
        ("ИТОГО:", total_with_profit),
        ("ТЕХНИЧЕСКИЙ ДИРЕКТОР", tech_dir),
        ("ИТОГО за наличку:", cash),
        ("безнал:", round(cash * COEFFS["noncash"], 2)),
    ]

    for label, amount in totals:
        is_final = "наличку" in label or "безнал" in label
        ws.merge_cells(f"A{row}:B{row}")
        ws.cell(row=row, column=3, value=label).font = TOTAL_FONT if is_final else NORMAL_FONT
        ws.cell(row=row, column=3).border = THIN_BORDER
        cell = ws.cell(row=row, column=4, value=amount)
        cell.font = TOTAL_FONT if is_final else NORMAL_FONT
        cell.number_format = '#,##0'
        cell.border = THIN_BORDER
        if is_final or "ИТОГО" in label:
            cell.fill = TOTAL_FILL
        ws.cell(row=row, column=5, value="").border = THIN_BORDER
        ws.cell(row=row, column=6, value="").border = THIN_BORDER
        row += 1

    # Ширина колонок
    widths = [30, 20, 35, 14, 12, 18]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


def _write_summary(ws, result: PipelineResult):
    """Сводный лист."""
    ws.merge_cells("A1:E1")
    ws["A1"].value = "СВОДНЫЙ РАСЧЁТ ПО ВСЕМ ПОМЕЩЕНИЯМ"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)

    ws.merge_cells("A2:E2")
    ws["A2"].value = f"Адрес: {result.project_address}"

    row = 4
    headers = ["Помещение", "Модулей", "Материалы (AI)", "ЛДСП листов", "Кромка, м"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
    row += 1

    total_ldsp = 0
    total_edge = 0
    for room in result.rooms:
        if not room.modules:
            continue
        q = calculate_quantities(room.modules, room.room_name, room.materials)
        total_ldsp += q.ldsp_sheets
        total_edge += q.edge_08_m + q.edge_04_m

        data = [
            room.room_name[:30],
            len(room.modules),
            ", ".join(room.materials[:2]) if room.materials else "",
            q.ldsp_sheets,
            round(q.edge_08_m + q.edge_04_m, 1),
        ]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.font = NORMAL_FONT
            cell.border = THIN_BORDER
        row += 1

    # Итого
    ws.merge_cells(f"A{row}:C{row}")
    ws.cell(row=row, column=1, value="ИТОГО:").font = TOTAL_FONT
    ws.cell(row=row, column=1).fill = TOTAL_FILL
    ws.cell(row=row, column=4, value=total_ldsp).font = TOTAL_FONT
    ws.cell(row=row, column=4).fill = TOTAL_FILL
    ws.cell(row=row, column=5, value=round(total_edge, 1)).font = TOTAL_FONT
    ws.cell(row=row, column=5).fill = TOTAL_FILL

    widths = [28, 10, 30, 12, 12]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w
