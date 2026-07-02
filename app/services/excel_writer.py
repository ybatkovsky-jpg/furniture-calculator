"""
Генератор спецификации в Excel из результатов AI-конвейера.

Создаёт Excel-файл с:
- Одна вкладка на помещение
- Модули с размерами, материалами, количеством
- Структура как в шаблоне Таблица для расчетов.xlsx
- Итоговый лист-сводка по всем помещениям
"""

import logging
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.services.full_pipeline import PipelineResult, RoomSpec

logger = logging.getLogger(__name__)

# Стили
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
SECTION_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
SECTION_FONT = Font(name="Arial", size=10, bold=True)
NORMAL_FONT = Font(name="Arial", size=9)
TOTAL_FONT = Font(name="Arial", size=11, bold=True)
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def generate_spec_excel(
    pipeline_result: PipelineResult,
    output_path: str = "Спецификация_AI.xlsx",
    template_path: Optional[str] = None,
) -> str:
    """
    Сгенерировать Excel-спецификацию из результатов AI-конвейера.

    Args:
        pipeline_result: результат FullPipeline.process()
        output_path: путь для сохранения
        template_path: путь к шаблону (для копирования структуры)

    Returns:
        путь к созданному файлу
    """
    wb = Workbook()
    # Удаляем дефолтный лист
    wb.remove(wb.active)

    # ── Вкладки по помещениям ──
    for room in pipeline_result.rooms:
        if not room.modules:
            continue  # пропускаем информационные секции

        sheet_name = _clean_sheet_name(room.room_name)[:31]  # Excel: макс 31 символ
        ws = wb.create_sheet(title=sheet_name)
        _write_room_sheet(ws, room, pipeline_result)

    # ── Сводный лист ──
    ws_summary = wb.create_sheet(title="СВОДКА")
    _write_summary_sheet(ws_summary, pipeline_result)

    # ── Сохраняем ──
    wb.save(output_path)
    logger.info(f"✅ Спецификация сохранена: {output_path}")
    return output_path


def _clean_sheet_name(name: str) -> str:
    """Очистить имя для вкладки Excel."""
    # Убираем запрещённые символы: \ / * ? : [ ]
    for ch in r'[]:*?/\\':
        name = name.replace(ch, "")
    return name.strip() or "Лист"


def _write_room_sheet(ws, room: RoomSpec, result: PipelineResult):
    """Заполнить вкладку одного помещения."""
    # Заголовок
    ws.merge_cells("A1:G1")
    title_cell = ws["A1"]
    title_cell.value = f"СПЕЦИФИКАЦИЯ: {room.room_name}"
    title_cell.font = Font(name="Arial", size=14, bold=True)
    title_cell.alignment = Alignment(horizontal="center")

    # Инфо
    ws.merge_cells("A2:G2")
    info_parts = []
    if result.project_address:
        info_parts.append(result.project_address)
    if room.page:
        info_parts.append(f"Стр. {room.page}")
    ws["A2"].value = " | ".join(info_parts)
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")

    # Шапка таблицы
    headers = ["Тип модуля", "Ширина, мм", "Глубина, мм", "Высота, мм",
               "Кол-во", "Стекло", "Примечание"]
    row = 4
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        cell.border = THIN_BORDER

    # Данные модулей
    type_names = {
        "lower_base": "Нижняя база", "upper_base": "Верхняя база",
        "penal": "Пенал", "column": "Колонна",
        "corner": "Угловой модуль", "tumbler": "Тумба",
    }

    for i, module in enumerate(room.modules):
        row = 5 + i
        data = [
            type_names.get(module.type, module.type),
            module.width, module.depth, module.height,
            module.quantity,
            "Да" if module.has_glass else "Нет",
            "УГЛОВОЙ" if module.is_corner else "",
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = NORMAL_FONT
            cell.border = THIN_BORDER
            if col in (2, 3, 4, 5):
                cell.alignment = Alignment(horizontal="center")

    # Материалы
    row = 5 + len(room.modules) + 2
    if room.materials:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="МАТЕРИАЛЫ").font = SECTION_FONT
        ws.cell(row=row, column=1).fill = SECTION_FILL
        row += 1
        for mat in room.materials[:10]:
            ws.merge_cells(f"A{row}:G{row}")
            ws.cell(row=row, column=1, value=f"• {mat}").font = NORMAL_FONT
            row += 1

    # Примечания
    if room.notes:
        row += 1
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="ПРИМЕЧАНИЯ").font = SECTION_FONT
        ws.cell(row=row, column=1).fill = SECTION_FILL
        row += 1
        for note in room.notes[:5]:
            ws.merge_cells(f"A{row}:G{row}")
            ws.cell(row=row, column=1, value=f"• {note[:120]}").font = NORMAL_FONT
            row += 1

    # Ширина колонок
    widths = [18, 12, 12, 12, 10, 8, 20]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


def _write_summary_sheet(ws, result: PipelineResult):
    """Сводный лист по всем помещениям."""
    # Заголовок
    ws.merge_cells("A1:F1")
    ws["A1"].value = "СВОДНАЯ СПЕЦИФИКАЦИЯ"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")

    # Метаданные
    row = 3
    for label, value in [
        ("Адрес:", result.project_address),
        ("Дизайнер:", str(result.designer) if result.designer else ""),
        ("Страниц в альбоме:", result.total_pages),
        ("Помещений:", len([r for r in result.rooms if r.modules])),
    ]:
        ws.cell(row=row, column=1, value=label).font = Font(name="Arial", size=10, bold=True)
        ws.cell(row=row, column=2, value=value).font = NORMAL_FONT
        row += 1

    row += 1

    # Шапка таблицы
    headers = ["Помещение", "Модулей", "Типы модулей", "Материалы", "Уверенность", "Стр."]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        cell.border = THIN_BORDER
    row += 1

    # Данные
    for room in result.rooms:
        if not room.modules:
            continue
        types = set(m.type for m in room.modules)
        type_names = {
            "lower_base": "Нижняя база", "upper_base": "Верхняя база",
            "penal": "Пенал", "corner": "Угловой",
        }
        type_str = ", ".join(type_names.get(t, t) for t in types)
        mat_str = ", ".join(room.materials[:3])

        data = [
            room.room_name,
            len(room.modules),
            type_str,
            mat_str,
            room.confidence or "",
            room.page,
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = NORMAL_FONT
            cell.border = THIN_BORDER
        row += 1

    # Ширина колонок
    widths = [25, 10, 25, 35, 12, 8]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


# ================================================================
# БЫСТРЫЙ ЗАПУСК
# ================================================================

async def run_and_generate(
    pdf_path: str,
    output_excel: str = "Спецификация_AI.xlsx",
    max_pages: int = 10,
):
    """Запустить конвейер и сразу сгенерировать Excel."""
    from app.services.full_pipeline import run_pipeline

    print(f"🚀 Запуск конвейера ({max_pages} стр.)...")
    result = await run_pipeline(pdf_path, max_pages=max_pages)

    print(f"📊 Генерация Excel: {output_excel}")
    path = generate_spec_excel(result, output_path=output_excel)

    # Краткий отчёт
    rooms_with_modules = [r for r in result.rooms if r.modules]
    total_modules = sum(len(r.modules) for r in rooms_with_modules)

    print()
    print("=" * 60)
    print(f"✅ ГОТОВО: {path}")
    print(f"   Вкладок: {len(rooms_with_modules)}")
    print(f"   Модулей: {total_modules}")
    print(f"   Адрес: {result.project_address}")
    print("=" * 60)
    return path
