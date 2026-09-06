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
    detect_material_properties,
)
from app.services.project_spec import (
    ProjectSpec,
    load_project_spec,
    apply_spec_to_quantities,
    print_spec_summary,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# МАППИНГ: категория материала → ключевые слова в колонке A → поле MaterialQuantities
# ═══════════════════════════════════════════════════════════════════

# (искомые_слова_в_наименовании, поле_в_MaterialQuantities, единица_измерения, множитель)
# множитель применяется к значению из MaterialQuantities перед записью в ячейку
# Загружается из templates/row_mapping.json (обязательный файл)
import json as _json_mod
_row_config_path = Path(__file__).parent.parent.parent / "templates" / "row_mapping.json"
if not _row_config_path.exists():
    raise FileNotFoundError(
        f"Файл row_mapping.json не найден: {_row_config_path}. Создайте его."
    )
with open(_row_config_path, "r", encoding="utf-8") as _f:
    _data = _json_mod.load(_f)
ROW_MAPPING: List[Tuple[List[str], str, str, float]] = [
    (item["keywords"], item["field"], item.get("unit", ""), item.get("multiplier", 1.0))
    for item in _data.get("mappings", [])
]
logger.info(f"📋 ROW_MAPPING из JSON: {len(ROW_MAPPING)} строк")


def _find_row_for_material(ws, keywords: List[str]) -> Optional[int]:
    """
    Найти номер строки в листе по ключевым словам в колонках A (Наименование)
    и B (Цвет). Ищет точное совпадение, затем нечёткое (fuzzy).
    Все keywords должны содержаться в этом тексте.
    """
    # 1. Точный поиск
    for row in range(1, ws.max_row + 1):
        cell_a = str(ws.cell(row=row, column=1).value or "")
        cell_b = str(ws.cell(row=row, column=2).value or "")
        combined = f"{cell_a} | {cell_b}"
        if not cell_a and not cell_b:
            continue
        # Все ключевые слова должны присутствовать (регистронезависимо)
        if all(kw.lower() in combined.lower() for kw in keywords):
            return row

    # 2. Нечёткий поиск (fallback)
    return _find_row_for_material_fuzzy(ws, keywords)


def _find_row_for_material_fuzzy(
    ws, keywords: List[str], threshold: float = 0.75
) -> Optional[int]:
    """
    Fallback: нечёткий поиск через SequenceMatcher.
    Используется когда точный поиск не дал результата.
    """
    from difflib import SequenceMatcher

    query = " ".join(keywords).lower()
    best_row, best_score = None, 0.0

    for row in range(1, ws.max_row + 1):
        cell_a = str(ws.cell(row=row, column=1).value or "")
        cell_b = str(ws.cell(row=row, column=2).value or "")
        combined = f"{cell_a} | {cell_b}".lower()

        if not cell_a and not cell_b:
            continue

        score = SequenceMatcher(None, query, combined).ratio()
        if score > best_score:
            best_score, best_row = score, row

    if best_score >= threshold and best_row:
        logger.info(
            f"🎯 Fuzzy match: «{query[:60]}...» → R{best_row} (score={best_score:.2f})"
        )
        return best_row

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

    # Определяем свойства материала через ЕДИНУЮ функцию
    mat_props = detect_material_properties(materials)
    is_texture = mat_props["surface"] == "texture"

    # ЛДСП — распределяем по бренду и текстуре
    if q.ldsp_sheets > 0:
        brand = q.ldsp_brand or mat_props.get("brand", "EGGER")
        if is_texture:
            if brand == "EXTRAVERT":
                m["ldsp_sheets_texture_extravert"] = q.ldsp_sheets
            elif brand == "LAMARTY":
                m["ldsp_sheets_texture_lamarty"] = q.ldsp_sheets
            else:
                m["ldsp_sheets_texture"] = q.ldsp_sheets
        else:
            if brand == "EXTRAVERT":
                m["ldsp_sheets_plain_extravert"] = q.ldsp_sheets
            elif brand == "LAMARTY":
                m["ldsp_sheets_plain_lamarty"] = q.ldsp_sheets
            elif brand == "ТОМЛЕСДРЕВ":
                m["ldsp_sheets_tomlesdrev"] = q.ldsp_sheets
            else:
                m["ldsp_sheets_plain"] = q.ldsp_sheets

    # МДФ — только для крашеных/лакокраска фасадов (ПВХ и EMDIWAY — готовые, МДФ включён)
    if q.mdf_sheets > 0:
        is_painted = mat_props["facade_type"] in ("paint_matte", "paint_gloss")
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
        ft = mat_props["facade_type"]
        if ft == "emdiway_titan":
            m["facades_m2_emdiway_titan"] = round(q.facades_area_m2, 1)
        elif ft == "emdiway":
            m["facades_m2_emdiway"] = round(q.facades_area_m2, 1)
        elif ft == "paint_matte":
            m["facades_m2_paint_matte"] = round(q.facades_area_m2, 1)
        elif ft == "paint_gloss":
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
        if q.drying_rack_type == "boyard":
            m["drying_rack_boyard"] = q.drying_rack_count
        else:
            m["drying_rack_alba"] = q.drying_rack_count
    if q.bottle_holder_count > 0:
        if q.bottle_holder_type == "kvadro":
            m["bottle_holder_kvadro"] = q.bottle_holder_count
        else:
            m["bottle_holder_flora"] = q.bottle_holder_count
    if q.cutlery_tray_count > 0:
        m["cutlery_tray"] = q.cutlery_tray_count
    if q.hygienic_mat_count > 0:
        m["hygienic_mat"] = q.hygienic_mat_count

    # ── НОВЫЕ ПОЛЯ (v2.0) ──

    # Столешница
    if q.countertop_length_m > 0:
        m["countertop_length_m"] = round(q.countertop_length_m, 1)

    # Крепёж
    if q.confirmat_count > 0:
        m["confirmat_count"] = q.confirmat_count
    if q.adjustable_feet > 0:
        m["adjustable_feet"] = q.adjustable_feet
    if q.wall_mounts > 0:
        m["wall_mounts"] = q.wall_mounts
    if q.plinth_strips > 0:
        m["plinth_strips"] = q.plinth_strips

    # Штанги
    if q.rods_round_count > 0:
        m["rods_round"] = q.rods_round_count
    if q.rods_rectangular_count > 0:
        m["rods_rectangular"] = q.rods_rectangular_count

    # Ручки
    if q.handles_count > 0:
        m["handles_count"] = q.handles_count

    # Плёнка ПВХ
    if q.pvc_film_m2 > 0:
        m["pvc_film_m2"] = round(q.pvc_film_m2, 1)

    # Кромка МДФ
    if q.edge_mdf_1mm_m > 0:
        m["edge_mdf_1mm_m"] = q.edge_mdf_1mm_m

    # Бренд ЛДСП (для выбора правильного размера листа)
    if q.ldsp_brand:
        m["ldsp_brand"] = q.ldsp_brand

    return m


def fill_template_from_pipeline(
    pipeline_result: PipelineResult,
    template_path: str,
    output_path: str,
    project_spec: Optional[ProjectSpec] = None,
) -> str:
    """
    Главная функция: заполнить шаблон на основе результатов конвейера.

    Для каждого помещения с модулями:
    1. Копируется лист-шаблон «Рассчет»
    2. Рассчитываются количества
    3. Применяется спецификация проекта (project_spec.yaml)
    4. Заполняется колонка E (Количество)

    Args:
        pipeline_result: результат FullPipeline.process()
        template_path: путь к файлу-шаблону (Таблица для расчетов пустая.xlsx)
        output_path: путь для сохранения заполненного файла
        project_spec: загруженная спецификация проекта (опционально)

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
    unfilled_items = []  # собираем позиции, для которых не нашлась строка
    spec_applied = False  # спецификацию применяем только к ПЕРВОЙ комнате

    for i, room in enumerate(rooms_with_modules):
        sheet_name = _clean_sheet_name(room.room_name)[:31]

        # Копируем лист-шаблон
        new_ws = wb.copy_worksheet(template_ws)
        new_ws.title = sheet_name

        # Обновляем заголовок (A1) — заменяем на название помещения
        _update_title(new_ws, room, pipeline_result)

        # Рассчитываем количества (spec подавляет автодобавление)
        q = calculate_quantities(room.modules, room.room_name, room.materials, zone_type=room.zone_type,
                                 has_spec=(project_spec is not None))

        # Применяем спецификацию проекта ТОЛЬКО к первой комнате (не дублируем!)
        if project_spec and not spec_applied:
            n_added = apply_spec_to_quantities(project_spec, q, room.room_name)
            if n_added > 0:
                spec_applied = True
                # Если spec добавил GOLA — убираем ручки
                if q.gola_horizontal_pcs > 0 or q.gola_vertical_pcs > 0:
                    q.handles_count = 0

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
                    # Строка не найдена — собираем для отчёта
                    unfilled_items.append({
                        "room": sheet_name,
                        "material": " + ".join(keywords[:2]),
                        "expected_value": f"{value * multiplier} {unit}",
                        "field": field,
                    })
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

    # ── Удаляем служебные листы шаблона ──
    _remove_template_sheets(wb)

    # ── Отчёт о незаполненных строках ──
    if unfilled_items:
        _add_problems_sheet(wb, unfilled_items)

    # ── Контроль качества ──
    _add_quality_sheet(wb, pipeline_result)

    # ── Список распознанной мебели по комнатам ──
    _add_modules_sheet(wb, rooms_with_modules)

    # Сохраняем
    wb.save(output_path)
    logger.info(f"💾 Сохранено: {output_path}")
    return output_path


def _remove_template_sheets(wb: Workbook):
    """
    Удалить служебные листы шаблона из финального файла:
    — «Рассчет» (образец, с которого копировали)
    — «РАСЧЕТ ЗЕРКАЛ» / «РАСЧЕТ СТЕКОЛ» (неактуальные данные)
    """
    TO_REMOVE = {"Рассчет", "РАСЧЕТ ЗЕРКАЛ", "РАСЧЕТ СТЕКОЛ", "РАСЧЁТ ЗЕРКАЛ", "РАСЧЁТ СТЕКОЛ"}

    removed = []
    for name in list(wb.sheetnames):
        if name in TO_REMOVE:
            del wb[name]
            removed.append(name)

    if removed:
        logger.info(f"🗑️  Удалены служебные листы: {', '.join(removed)}")


def _add_quality_sheet(wb: Workbook, pipeline_result: PipelineResult):
    """
    Добавить лист «✅ Контроль качества» с оценкой каждого помещения.

    Содержит:
    - Название помещения, страница
    - Количество модулей, уверенность AI
    - Quality Score (0..1), флаги
    - Рекомендация оператору (🔴🟡🟢)
    """
    from openpyxl.styles import Font, PatternFill, Alignment

    sheet_name = "✅ Контроль качества"

    # Удаляем старый лист если есть (при повторном запуске)
    if sheet_name in [ws.title for ws in wb.worksheets]:
        del wb[sheet_name]

    ws = wb.create_sheet(sheet_name, 0)  # вставляем первым листом

    # Стили
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")

    # Заголовки
    headers = [
        ("A", "Помещение", 30),
        ("B", "Стр.", 6),
        ("C", "Модулей", 10),
        ("D", "Уверенность AI", 14),
        ("E", "Quality Score", 13),
        ("F", "Флаги", 45),
        ("G", "Рекомендация", 25),
    ]
    for col_letter, title, width in headers:
        cell = ws[f"{col_letter}1"]
        cell.value = title
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[col_letter].width = width

    # Данные по каждому помещению
    for i, room in enumerate(pipeline_result.rooms, start=2):
        ws.cell(row=i, column=1, value=room.room_name)
        ws.cell(row=i, column=2, value=room.page if room.page else "")
        ws.cell(row=i, column=3, value=len(room.modules))
        ws.cell(row=i, column=4, value=room.confidence or "—")

        # Quality Score с цветовой индикацией
        score_cell = ws.cell(row=i, column=5)
        score_cell.value = room.quality_score
        score_cell.number_format = "0%"
        score_cell.alignment = Alignment(horizontal="center")

        # Флаги
        ws.cell(row=i, column=6, value="; ".join(room.quality_flags) if room.quality_flags else "—")

        # Рекомендация
        if room.quality_score < 0.5:
            recommendation = "🔴 ПЕРЕПРОВЕРИТЬ"
            row_fill = red_fill
        elif room.quality_score < 0.8:
            recommendation = "🟡 Проверить"
            row_fill = yellow_fill
        else:
            recommendation = "🟢 ОК"
            row_fill = green_fill

        ws.cell(row=i, column=7, value=recommendation)

        # Подсветка всей строки
        for col in range(1, 8):
            ws.cell(row=i, column=col).fill = row_fill

    # Итоговая строка
    total_row = len(pipeline_result.rooms) + 2
    avg_score = (
        sum(r.quality_score for r in pipeline_result.rooms) / max(len(pipeline_result.rooms), 1)
    )
    ws.cell(row=total_row, column=1, value="ИТОГО").font = Font(bold=True)
    ws.cell(row=total_row, column=3, value=sum(len(r.modules) for r in pipeline_result.rooms))
    score_cell = ws.cell(row=total_row, column=5, value=avg_score)
    score_cell.number_format = "0%"
    score_cell.font = Font(bold=True)

    logger.info(f"📊 Лист «Контроль качества»: {len(pipeline_result.rooms)} помещений, средний score={avg_score:.0%}")


# Человекочитаемые названия типов модулей для листа «🧩 Модули (распознано)».
MODULE_TYPE_LABELS_RU = {
    "lower_base": "Нижняя база",
    "upper_base": "Верхняя база",
    "penal": "Пенал",
    "corner": "Угловой модуль",
    "column": "Колонна (техника)",
    "tumbler": "Тумба",
    "tall_cabinet": "Высокий шкаф",
    "wardrobe": "Шкаф-купе",
    "shelf_unit": "Стеллаж / открытые полки",
    "vanity": "Тумба под раковину",
    "drawer_unit": "Модуль с ящиками",
    "open_unit": "Открытый модуль",
    "wall_panel": "Декоративная панель",
}


def _module_ru_name(mtype: Optional[str]) -> str:
    """Тип модуля → русское название (для оператора)."""
    return MODULE_TYPE_LABELS_RU.get(mtype, mtype or "?")


def _add_modules_sheet(wb: Workbook, rooms_with_modules: list):
    """
    Лист «🧩 Модули (распознано)»: КАКАЯ мебель и с какими габаритами
    посчитана по каждому помещению (одна строка = один распознанный модуль).
    Оператор видит состав мебели, а не только агрегированные материалы.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheet_name = "🧩 Модули (распознано)"
    if sheet_name in [ws.title for ws in wb.worksheets]:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)  # в конец книги

    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    room_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    alt_fill = PatternFill(start_color="F2F7FC", end_color="F2F7FC", fill_type="solid")

    headers = [
        "Помещение", "Мебель (модуль)", "Габарит Ш×Г×В, мм", "Кол-во",
        "Дверей (фасадов)", "Ящиков", "Стекло", "Угловой", "Материалы",
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    row = 2
    prev_room = None
    for room in rooms_with_modules:
        rname = (room.room_name or "?")[:31]
        for m in room.modules or []:
            qty = getattr(m, "quantity", 1) or 1
            facades = (m.facades or {}).get("count") if isinstance(m.facades, dict) else None
            drawers = (m.drawers or {}).get("count") if isinstance(m.drawers, dict) else None
            vals = [
                rname if rname != prev_room else "",
                _module_ru_name(getattr(m, "type", "")),
                f"{getattr(m, 'width', 0)}×{getattr(m, 'depth', 0)}×{getattr(m, 'height', 0)}",
                qty,
                facades if facades is not None else "",
                drawers if drawers is not None else "",
                "✅" if getattr(m, "has_glass", False) else "",
                "✅" if getattr(m, "is_corner", False) else "",
                ", ".join(room.materials or [])[:80],
            ]
            for col, v in enumerate(vals, 1):
                ws.cell(row=row, column=col, value=v)
            if rname != prev_room:
                for col in range(1, len(headers) + 1):
                    ws.cell(row=row, column=col).fill = room_fill
                prev_room = rname
            elif row % 2 == 0:
                for col in range(1, len(headers) + 1):
                    ws.cell(row=row, column=col).fill = alt_fill
            row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="Нет распознанных модулей")

    widths = [16, 24, 18, 8, 13, 8, 8, 8, 50]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = "A2"
    if row > 2:
        ws.auto_filter.ref = f"A1:I{row - 1}"

    logger.info(f"🧩 Лист «Модули (распознано)»: {row - 2} строк мебели")


def _add_problems_sheet(wb: Workbook, unfilled_items: list):
    """
    Добавить лист «⚠ Проблемы» с незаполненными позициями.

    Содержит строки, для которых не нашлось соответствия в шаблоне.
    Оператор должен внести эти позиции вручную или добавить строку в шаблон.
    """
    from openpyxl.styles import Font, PatternFill

    sheet_name = "⚠ Проблемы"

    # Удаляем старый если есть
    if sheet_name in [ws.title for ws in wb.worksheets]:
        del wb[sheet_name]

    # Вставляем в начало
    idx = min(2, len(wb.sheetnames))
    ws = wb.create_sheet(sheet_name, idx)

    # Стили
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    warn_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

    # Заголовки
    ws["A1"] = "Помещение"
    ws["B1"] = "Материал (не найдена строка в шаблоне)"
    ws["C1"] = "Ожидаемое значение"
    ws["D1"] = "Действие оператора"

    for col in range(1, 5):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill

    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 45
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 35

    # Данные
    for i, item in enumerate(unfilled_items, start=2):
        ws.cell(row=i, column=1, value=item["room"])
        ws.cell(row=i, column=2, value=item["material"])
        ws.cell(row=i, column=3, value=item["expected_value"])
        ws.cell(row=i, column=4, value="Внести вручную или добавить строку в шаблон")

        for col in range(1, 5):
            ws.cell(row=i, column=col).fill = warn_fill

    logger.warning(f"⚠️ Незаполненных позиций: {len(unfilled_items)} — см. лист «⚠ Проблемы»")


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
    spec_path: str = None,
) -> str:
    """
    Запустить полный цикл: PDF → AI-конвейер → спецификация → заполнение шаблона.

    Args:
        pdf_path: путь к PDF с чертежами
        template_path: путь к файлу-шаблону Excel
        output_path: путь для сохранения результата
        max_pages: максимальное число страниц для Vision-анализа
        spec_path: путь к project_spec.yaml (опционально)

    Returns:
        путь к заполненному Excel-файлу
    """
    from app.services.full_pipeline import run_pipeline, print_result

    print(f"🚀 Запуск конвейера: {Path(pdf_path).name}")
    print(f"   Макс. страниц для анализа: {max_pages}")

    # Шаг 0: Загрузка спецификации проекта
    project_spec = None
    if spec_path:
        print(f"   Спецификация: {Path(spec_path).name}")
        project_spec = load_project_spec(spec_path)
        print_spec_summary(project_spec)
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
        project_spec=project_spec,
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
