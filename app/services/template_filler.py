"""
Заполнение шаблона «Таблица для расчетов» на основе результатов AI-конвейера.

Логика:
1. Для каждого помещения из PipelineResult копируется лист-шаблон «Рассчет»
2. Рассчитываются количества материалов через quantity_calc
3. Заполняется ТОЛЬКО колонка E (Количество) в нужных строках
4. Пустые строки сохраняются, структура шаблона не меняется
"""

import logging
import math
import re
import copy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openpyxl import Workbook, worksheet
from openpyxl.utils import get_column_letter

from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.quantity_calc import (
    MaterialQuantities,
    calculate_quantities,
    fill_template_for_room,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# МАППИНГ: категория материала → ключевые слова в колонке A → поле MaterialQuantities
# ═══════════════════════════════════════════════════════════════════

# (искомые_слова_в_наименовании, поле_в_MaterialQuantities, единица_измерения, множитель)
# множитель применяется к значению из MaterialQuantities перед записью в ячейку
ROW_MAPPING: List[Tuple[List[str], str, str, float]] = [
    # ── ЛДСП ──
    (["EGGER ЛДСП", "однотон"], "ldsp_sheets_plain", "листов", 1.0),
    (["EGGER ЛДСП", "текстура"], "ldsp_sheets_texture", "листов", 1.0),
    (["EGGER ЛМДФ", "древесн"], "mdf_sheets", "листов", 1.0),
    (["EXTRAVERT", "однотон"], "ldsp_sheets_plain_extravert", "листов", 1.0),
    (["EXTRAVERT", "текстуры"], "ldsp_sheets_texture_extravert", "листов", 1.0),
    (["LAMARTY", "однотон"], "ldsp_sheets_plain_lamarty", "листов", 1.0),
    (["LAMARTY", "текстуры"], "ldsp_sheets_texture_lamarty", "листов", 1.0),
    (["ТОМЛЕСДРЕВ", "однотон"], "ldsp_sheets_tomlesdrev", "листов", 1.0),

    # ── КРОМКА ──
    (["EGGER 0,4*19"], "edge_04_m", "м.п.", 1.0),
    (["EGGER 0,8*19"], "edge_08_m", "м.п.", 1.0),
    (["EGGER 2*19"], "edge_2_m", "м.п.", 1.0),
    (["EGGER 2*35"], "edge_2_35_m", "м.п.", 1.0),
    (["EXTRAVERT 0,4*19"], "edge_04_m_extravert", "м.п.", 1.0),
    (["EXTRAVERT 0,8*19"], "edge_08_m_extravert", "м.п.", 1.0),
    (["EXTRAVERT 2*19"], "edge_2_m_extravert", "м.п.", 1.0),

    # ── ХДФ ──
    (["ЛХДФ"], "hdf_sheets", "листов", 1.0),

    # ── МДФ / Фасады ──
    (["EMDIWAY (однотонные матовые"], "facades_m2_emdiway", "м²", 1.0),
    (["EMDIWAY", "Titan 3108"], "facades_m2_emdiway_titan", "м²", 1.0),
    (["ЛЕМАКОМ", "ФАСАДЫ одностор. ПВХ 16 мм, фрезеровка S"], "facades_m2_pvh_s", "м²", 1.0),
    (["ЛЕМАКОМ", "ФАСАДЫ одностор. ПВХ 16 мм, фрезеровка K"], "facades_m2_pvh_k", "м²", 1.0),
    (["Лемаком", "Прямой односторон. МАТОВЫЙ"], "facades_m2_paint_matte", "м²", 1.0),
    (["Лемаком", "Прямой односторон. ГЛЯНЕЦ"], "facades_m2_paint_gloss", "м²", 1.0),

    # ── Петли FIRMAX ──
    (["Петля FIRMAX с доводчиком (накладная, вкладная)"], "hinges_firmax_closer", "шт", 1.0),
    (["Петля FIRMAX без доводчика (вкладная, накладная)"], "hinges_firmax_no_closer", "шт", 1.0),

    # ── Ящики BLUM ──
    (["Ящик внутренний TANDEMBOX", "МФ-ГРУПП"], "drawers_internal_tandembox", "шт", 1.0),
    (["Ящик стандартный для низких фасадов TANDEMBOX"], "drawers_tandembox", "шт", 1.0),
    (["Ящик с 1м релингом для средних фасадов TANDEMBOX"], "drawers_tandembox_mid", "шт", 1.0),
    (["Ящик с 2мя релингами для высоких фасадов TANDEMBOX"], "drawers_tandembox_high", "шт", 1.0),
    (["Ящик стандартный для низких фасадов LEGRABOX"], "drawers_legrabox", "шт", 1.0),
    (["Ящик  для средних фасадов LEGRABOX 144мм"], "drawers_legrabox_mid", "шт", 1.0),
    (["Ящик  для высоких фасадов LEGRABOX 193 мм"], "drawers_legrabox_high", "шт", 1.0),

    # ── Ящики BOYARD ──
    (["Стандартный ящик тонкий СТАРТ h=86 мм для низких фасадов"], "drawers_boyard_start", "шт", 1.0),

    # ── Gola ──
    (["GOLA профиль горизонтальный 3 м (C,L)"], "gola_horizontal_3m", "шт", 1.0),
    (["GOLA профиль вертикальный боковой", "3 м"], "gola_vertical_3m", "шт", 1.0),

    # ── Подсветка ──
    (["Подсветка LED (Бухта 5м)"], "led_strip_5m", "шт", 1.0),
    (["Блок питания", "324"], "led_power_supply", "шт", 1.0),       # R324
    (["Датчик", "325"], "led_sensor", "шт", 1.0),                     # R325

    # ── Комплектующие ──
    (["Сушка для посуды Alba, в модуль на 900мм"], "drying_rack", "шт", 1.0),
    (["Выдвижная корзина 2-х уровн.(150) Flora BOYARD круглый пруток"], "bottle_holder_150", "шт", 1.0),
    (["Выдвижная корзина 2-х уровн.(200) Flora BOYARD круглый пруток"], "bottle_holder_200", "шт", 1.0),
    (["Лоток для столовых приборов"], "cutlery_tray", "шт", 1.0),

    # ── Стекло ──
    # Зеркало и стекло будут на отдельном листе «РАСЧЕТ ЗЕРКАЛ»
]


def _find_row_for_material(ws, keywords: List[str]) -> Optional[int]:
    """
    Найти номер строки в листе по ключевым словам в колонках A (Наименование)
    и B (Цвет). Ищет совпадение в объединённом тексте A + " | " + B.
    Все keywords должны содержаться в этом тексте.
    """
    for row in range(1, ws.max_row + 1):
        cell_a = str(ws.cell(row=row, column=1).value or "")
        cell_b = str(ws.cell(row=row, column=2).value or "")
        combined = f"{cell_a} | {cell_b}"
        if not cell_a and not cell_b:
            continue
        # Все ключевые слова должны присутствовать (регистронезависимо)
        if all(kw.lower() in combined.lower() for kw in keywords):
            return row
    return None


def _build_quantity_map(
    q: MaterialQuantities,
    materials: List[str],
    room_name: str,
) -> Dict[str, float]:
    """
    Построить словарь {ключ_поля: значение} на основе MaterialQuantities
    и информации о материалах/помещении.

    Ключи соответствуют именам в ROW_MAPPING.
    """
    m = {}

    # Определяем тип материала ЛДСП
    is_texture = False
    for mat in materials:
        mat_upper = mat.upper()
        # Текстура = древесный декор (H1/H3 префиксы EGGER) или явное указание
        if any(kw in mat_upper for kw in ["ТЕКСТУР", "ДРЕВЕСН", "WOOD", "ДУБ", "ОРЕХ", "ЯСЕНЬ"]):
            is_texture = True
            break
        if any(mat_upper.startswith(p) for p in ["H1", "H3"]):
            is_texture = True
            break

    # ЛДСП
    if is_texture:
        m["ldsp_sheets_texture"] = q.ldsp_sheets
    else:
        m["ldsp_sheets_plain"] = q.ldsp_sheets

    # МДФ — только для крашеных/лакокраска фасадов (ПВХ и EMDIWAY — готовые, МДФ включён)
    if q.mdf_sheets > 0:
        materials_upper = " ".join(materials).upper()
        # МДФ-листы нужны только если фасады крашеные (Лакокраска/МАТОВЫЙ/ГЛЯНЕЦ)
        is_painted = any(kw in materials_upper for kw in ["ЛАКОКРАСКА", "МАТОВЫЙ", "ГЛЯНЕЦ", "КРАШЕН"])
        if is_painted:
            m["mdf_sheets"] = q.mdf_sheets

    # Кромка — значения уже округлены в quantity_calc
    if q.edge_04_m > 10:
        m["edge_04_m"] = q.edge_04_m
    if q.edge_08_m > 0:
        m["edge_08_m"] = q.edge_08_m
    if q.edge_2_m > 0:
        m["edge_2_m"] = q.edge_2_m

    # ХДФ
    if q.hdf_sheets > 0:
        m["hdf_sheets"] = q.hdf_sheets

    # Фасады
    if q.facades_area_m2 > 0:
        materials_upper = " ".join(materials).upper()
        if "EMDIWAY" in materials_upper:
            if "TITAN" in materials_upper:
                m["facades_m2_emdiway_titan"] = round(q.facades_area_m2, 1)
            else:
                m["facades_m2_emdiway"] = round(q.facades_area_m2, 1)
        elif "ЛАКОКРАСКА" in materials_upper or "МАТОВ" in materials_upper:
            m["facades_m2_paint_matte"] = round(q.facades_area_m2, 1)
        elif "ГЛЯНЕЦ" in materials_upper:
            m["facades_m2_paint_gloss"] = round(q.facades_area_m2, 1)
        else:
            m["facades_m2_pvh_s"] = round(q.facades_area_m2, 1)

    # Петли
    if q.hinges_count > 0:
        m["hinges_firmax_closer"] = q.hinges_count

    # Ящики
    if q.drawers_count > 0:
        system = q.drawer_system
        if system == "Legrabox":
            m["drawers_legrabox"] = q.drawers_count
        elif system == "Tandembox":
            m["drawers_tandembox"] = q.drawers_count
        else:
            m["drawers_boyard_start"] = q.drawers_count

    # Внутренние ящики (для приборов)
    if q.drawers_internal_count > 0:
        m["drawers_internal_tandembox"] = q.drawers_internal_count

    # Gola — используем готовые штуки из quantity_calc
    if q.gola_horizontal_pcs > 0:
        m["gola_horizontal_3m"] = q.gola_horizontal_pcs
    if q.gola_vertical_pcs > 0:
        m["gola_vertical_3m"] = q.gola_vertical_pcs

    # LED
    if q.led_strip_m > 0:
        m["led_strip_5m"] = 1
    if q.led_power_supply > 0:
        m["led_power_supply"] = q.led_power_supply
    if q.led_sensor > 0:
        m["led_sensor"] = q.led_sensor

    # Комплектующие
    if q.drying_rack_count > 0:
        m["drying_rack"] = q.drying_rack_count
    if q.bottle_holder_count > 0:
        m["bottle_holder_150"] = q.bottle_holder_count
    if q.cutlery_tray_count > 0:
        m["cutlery_tray"] = q.cutlery_tray_count

    return m


def fill_template_from_pipeline(
    pipeline_result: PipelineResult,
    template_path: str,
    output_path: str,
) -> str:
    """
    Главная функция: заполнить шаблон на основе результатов конвейера.

    Для каждого помещения с модулями:
    1. Копируется лист-шаблон «Рассчет»
    2. Рассчитываются количества
    3. Заполняется колонка E (Количество)

    Args:
        pipeline_result: результат FullPipeline.process()
        template_path: путь к файлу-шаблону (Таблица для расчетов пустая.xlsx)
        output_path: путь для сохранения заполненного файла

    Returns:
        путь к созданному файлу
    """
    from openpyxl import load_workbook

    # Загружаем шаблон
    logger.info(f"📄 Загрузка шаблона: {template_path}")
    wb = load_workbook(template_path)

    template_ws = wb["Рассчет"]
    rooms_with_modules = [r for r in pipeline_result.rooms if r.modules]

    if not rooms_with_modules:
        logger.warning("⚠️ Нет помещений с модулями для заполнения")
        wb.save(output_path)
        return output_path

    # Для каждого помещения создаём копию листа-шаблона
    for i, room in enumerate(rooms_with_modules):
        sheet_name = _clean_sheet_name(room.room_name)[:31]

        # Копируем лист-шаблон
        new_ws = wb.copy_worksheet(template_ws)
        new_ws.title = sheet_name

        # Обновляем заголовок (A1) — заменяем на название помещения
        _update_title(new_ws, room, pipeline_result)

        # Рассчитываем количества
        q = calculate_quantities(room.modules, room.room_name, room.materials)

        # Строим мапу quantities
        qty_map = _build_quantity_map(q, room.materials, room.room_name)

        # Заполняем колонку E (Количество) в найденных строках
        filled_rows = []
        for keywords, field, unit, multiplier in ROW_MAPPING:
            value = qty_map.get(field)
            if value is not None and value > 0:
                row_num = _find_row_for_material(new_ws, keywords)
                if row_num:
                    final_value = value * multiplier
                    # Записываем ТОЛЬКО в колонку E (5)
                    cell = new_ws.cell(row=row_num, column=5)
                    cell.value = final_value
                    filled_rows.append((row_num, keywords[0], final_value, unit))
                    logger.debug(
                        f"  {sheet_name}: R{row_num} «{keywords[0][:40]}...» → "
                        f"{final_value} {unit}"
                    )
                else:
                    logger.debug(
                        f"  {sheet_name}: ⚠ не найдена строка для {keywords}"
                    )

        logger.info(
            f"✅ {sheet_name}: заполнено {len(filled_rows)} строк, "
            f"модулей={len(room.modules)}, "
            f"ЛДСП={q.ldsp_sheets} листов, "
            f"кромка={q.edge_08_m:.0f}+{q.edge_04_m:.0f} м"
        )

        # ── Добавляем рекомендации после финансовой секции ──
        if q.suggestions:
            _add_suggestions_section(new_ws, q)

    # ── Сводный лист ──
    if "СВОДКА" in [ws.title for ws in wb.worksheets]:
        _update_summary(wb, pipeline_result, rooms_with_modules)

    # Сохраняем
    wb.save(output_path)
    logger.info(f"💾 Сохранено: {output_path}")
    return output_path


def _clean_sheet_name(name: str) -> str:
    """Очистить имя для вкладки Excel (макс 31 символ, без запрещённых)."""
    for ch in r'[]:*?/\\':
        name = name.replace(ch, "")
    name = name.strip()
    return name[:31] if name else "Лист"


def _update_title(ws, room: RoomSpec, result: PipelineResult):
    """Обновить заголовок листа (A1:F2) с названием помещения."""
    # Очищаем старые заголовки в строке 1 (кроме A1 — его перезапишем)
    for col in range(1, 7):
        ws.cell(row=1, column=col).value = None

    # Заголовок A1
    title_text = f"РАСЧЁТ: {room.room_name}"
    ws["A1"].value = title_text

    # A2 — адрес
    if result.project_address:
        ws["A2"].value = result.project_address
    else:
        ws["A2"].value = ""


def _update_summary(wb: Workbook, result: PipelineResult, rooms: List[RoomSpec]):
    """Обновить сводный лист (если есть)."""
    if "СВОДКА" not in [ws.title for ws in wb.worksheets]:
        return
    pass


def _add_suggestions_section(ws, q: MaterialQuantities):
    """
    Добавить блок рекомендаций после финансовой секции (после строки ~362).
    """
    from openpyxl.styles import Font, PatternFill

    # Ищем последнюю использованную строку (финансовая секция заканчивается ~R360)
    suggest_start = ws.max_row + 3

    suggest_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    suggest_font = Font(name="Arial", size=10, italic=True)

    ws.merge_cells(f"A{suggest_start}:F{suggest_start}")
    cell = ws.cell(row=suggest_start, column=1, value="💡 РЕКОМЕНДАЦИИ ПО ЭРГОНОМИКЕ И КОМПЛЕКТАЦИИ")
    cell.font = Font(name="Arial", size=11, bold=True)
    cell.fill = suggest_fill

    for i, s in enumerate(q.suggestions):
        row = suggest_start + 1 + i
        ws.merge_cells(f"A{row}:F{row}")
        cell = ws.cell(row=row, column=1, value=s)
        cell.font = suggest_font


# ═══════════════════════════════════════════════════════════════════
# БЫСТРЫЙ ЗАПУСК: конвейер + заполнение шаблона
# ═══════════════════════════════════════════════════════════════════

async def run_pipeline_and_fill_template(
    pdf_path: str,
    template_path: str,
    output_path: str = "Расчет_заполненный.xlsx",
    max_pages: int = 20,
) -> str:
    """
    Запустить полный цикл: PDF → AI-конвейер → заполнение шаблона.

    Args:
        pdf_path: путь к PDF с чертежами
        template_path: путь к файлу-шаблону Excel
        output_path: путь для сохранения результата
        max_pages: максимальное число страниц для Vision-анализа

    Returns:
        путь к заполненному Excel-файлу
    """
    from app.services.full_pipeline import run_pipeline, print_result

    print(f"🚀 Запуск конвейера: {Path(pdf_path).name}")
    print(f"   Макс. страниц для анализа: {max_pages}")
    print()

    # Шаг 1: AI-конвейер
    result = await run_pipeline(pdf_path, max_pages=max_pages)

    if not result.success:
        print(f"❌ Конвейер не дал результатов. Ошибки: {result.errors}")
        return ""

    print_result(result)

    # Шаг 2: Заполнение шаблона
    print()
    print("📊 Заполнение шаблона...")
    path = fill_template_from_pipeline(
        pipeline_result=result,
        template_path=template_path,
        output_path=output_path,
    )

    rooms_filled = len([r for r in result.rooms if r.modules])
    total_modules = sum(len(r.modules) for r in result.rooms)

    print()
    print("=" * 60)
    print(f"✅ ГОТОВО: {path}")
    print(f"   Листов-помещений: {rooms_filled}")
    print(f"   Модулей: {total_modules}")
    print(f"   Адрес: {result.project_address or 'не определён'}")
    print("=" * 60)

    return path


# ═══════════════════════════════════════════════════════════════════
# КОНСОЛЬНЫЙ ЗАПУСК (для тестирования)
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio
    import sys

    # Пути по умолчанию
    pdf = sys.argv[1] if len(sys.argv) > 1 else (
        r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf"
    )
    template = sys.argv[2] if len(sys.argv) > 2 else (
        r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Таблица для расчетов пустая.xlsx"
    )
    output = sys.argv[3] if len(sys.argv) > 3 else "Расчет_заполненный.xlsx"

    asyncio.run(run_pipeline_and_fill_template(
        pdf_path=pdf,
        template_path=template,
        output_path=output,
        max_pages=15,
    ))
