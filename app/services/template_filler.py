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
# Загружается из templates/row_mapping.json, fallback — хардкод
import json as _json_mod
_row_config_path = Path(__file__).parent.parent.parent / "templates" / "row_mapping.json"
if _row_config_path.exists():
    try:
        with open(_row_config_path, "r", encoding="utf-8") as _f:
            _data = _json_mod.load(_f)
        ROW_MAPPING: List[Tuple[List[str], str, str, float]] = [
            (item["keywords"], item["field"], item.get("unit", ""), item.get("multiplier", 1.0))
            for item in _data.get("mappings", [])
        ]
        logger.info(f"📋 ROW_MAPPING из JSON: {len(ROW_MAPPING)} строк")
    except Exception as _e:
        logger.warning(f"Ошибка загрузки row_mapping.json: {_e} — fallback на хардкод")
        ROW_MAPPING = _get_default_mapping()
else:
    ROW_MAPPING = _get_default_mapping()


def _get_default_mapping() -> List[Tuple[List[str], str, str, float]]:
    """Хардкод-маппинг как fallback (синхронизирован с row_mapping.json)."""
    return [
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

    # ЛДСП
    if is_texture:
        m["ldsp_sheets_texture"] = q.ldsp_sheets
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

    for i, room in enumerate(rooms_with_modules):
        sheet_name = _clean_sheet_name(room.room_name)[:31]

        # Копируем лист-шаблон
        new_ws = wb.copy_worksheet(template_ws)
        new_ws.title = sheet_name

        # Обновляем заголовок (A1) — заменяем на название помещения
        _update_title(new_ws, room, pipeline_result)

        # Рассчитываем количества
        q = calculate_quantities(room.modules, room.room_name, room.materials)

        # Применяем спецификацию проекта (добавляет позиции, которые AI не видит)
        if project_spec:
            apply_spec_to_quantities(project_spec, q, room.room_name)

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
