"""
Заполнение шаблона «Таблица для расчетов» на основе результатов AI-конвейера.

Логика:
1. Для каждого помещения из PipelineResult копируется лист-шаблон «Рассчет»
2. Рассчитываются количества материалов через quantity_calc
3. Заполняется ТОЛЬКО колонка E (Количество) в нужных строках
4. Пустые строки сохраняются, структура шаблона не меняется
"""

import logging
from pathlib import Path

from openpyxl import Workbook

from app.domain.money import money_float, rub_float
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.price_catalog import PriceCatalogItem, find_price
from app.services.project_spec import (
    ProjectSpec,
    apply_spec_to_quantities,
    load_project_spec,
    print_spec_summary,
)
from app.services.quantity_calc import (
    MaterialQuantities,
    calculate_quantities,
    detect_material_properties,
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
ROW_MAPPING: list[tuple[list[str], str, str, float]] = [
    (item["keywords"], item["field"], item.get("unit", ""), item.get("multiplier", 1.0))
    for item in _data.get("mappings", [])
]
logger.info(f"📋 ROW_MAPPING из JSON: {len(ROW_MAPPING)} строк")


def _find_row_for_material(ws, keywords: list[str]) -> int | None:
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
    ws, keywords: list[str], threshold: float = 0.75
) -> int | None:
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
    materials: list[str],
    room_name: str,
) -> dict[str, float]:
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
            m["facades_m2_emdiway_titan"] = round(q.facades_area_m2, 3)
        elif ft == "emdiway":
            m["facades_m2_emdiway"] = round(q.facades_area_m2, 3)
        elif ft == "paint_matte":
            m["facades_m2_paint_matte"] = round(q.facades_area_m2, 3)
        elif ft == "paint_gloss":
            m["facades_m2_paint_gloss"] = round(q.facades_area_m2, 3)
        elif ft == "pvh":
            m["facades_m2_pvh_s"] = round(q.facades_area_m2, 3)
        else:
            # P1-17: тип фасада не выбран — не подставляем «ПВХ» и не
            # добавляем площадь в ЛДСП. Пусть оператор укажет тип явно.
            logger.warning(
                "Тип фасада не выбран (unknown): %.1f м² не тарифицированы",
                q.facades_area_m2,
            )

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

    # Столешница (День 4: без округления до 0.1 — чат считает по 0.01 м,
    # иначе Excel и чат расходятся на десятки рублей).
    if q.countertop_length_m > 0:
        m["countertop_length_m"] = round(q.countertop_length_m, 3)

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
        m["pvc_film_m2"] = round(q.pvc_film_m2, 3)

    # Кромка МДФ: P0-06 — одна позиция пишется ровно в одну строку шаблона.
    # Раньше шесть записей row_mapping использовали одно поле, и цикл заполнял
    # все найденные строки (6 м → 5 строк → 2 256 ₽ вместо 288 ₽).
    if q.edge_mdf_1mm_m > 0:
        mat_props = detect_material_properties(materials or [])
        ft = mat_props.get("facade_type")
        mdf_edge_field = {
            "emdiway": "edge_mdf_1mm_emdiway",
            "emdiway_titan": "edge_mdf_1mm_emdiway",
            "evo": "edge_mdf_1mm_evo",
            "agt": "edge_mdf_1mm_agt",
            "eterno": "edge_mdf_1mm_eterno",
            "smart": "edge_mdf_1mm_smart",
        }.get(str(ft), "edge_mdf_1mm_kromka")
        m[mdf_edge_field] = q.edge_mdf_1mm_m

    # Бренд ЛДСП (для выбора правильного размера листа)
    if q.ldsp_brand:
        m["ldsp_brand"] = q.ldsp_brand

    return m


def fill_template_from_pipeline(
    pipeline_result: PipelineResult,
    template_path: str,
    output_path: str,
    project_spec: ProjectSpec | None = None,
    pricing_policy=None,
    price_catalog: list[PriceCatalogItem] | None = None,
    price_book: dict[str, float] | None = None,
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
        price_catalog: активные позиции прайса (load_price_catalog); без него
            цены не подставляются, а позиции уходят в «⚠ Проблемы»
        price_book: те же цены, что чат берёт через load_price_book (ХДФ,
            столешница, крепёж, GOLA, LED, ручки)

    Returns:
        путь к созданному файлу
    """
    from openpyxl import load_workbook

    logger.warning(
        "fill_template_from_pipeline устарел (P0-02): Excel из снимка — "
        "fill_template_from_quote; старый путь оставлен только для CLI-отладки"
    )

    # P1-01: коэффициенты в финансовой секции — из единой политики, а не
    # зашитые в шаблон 0.8/0.55/0.08/0.05.
    from app.services.pricing_policy import coerce_policy

    policy = coerce_policy(pricing_policy)

    # Загружаем шаблон
    logger.info(f"📄 Загрузка шаблона: {template_path}")
    wb = load_workbook(template_path)
    # День 2: цены Excel — только из того же прайса БД, что и чат. Цена
    # шаблона больше не используется: если в прайсе нет однозначной позиции,
    # строка остаётся без цены и попадает в «⚠ Проблемы».
    catalog = list(price_catalog or [])
    if not catalog:
        logger.warning(
            "Прайс не передан в Excel: цены не подставляются, все позиции "
            "с количеством уйдут в «⚠ Проблемы»"
        )

    template_ws = wb["Рассчет"]
    rooms_with_modules = [r for r in pipeline_result.rooms if r.modules]

    if not rooms_with_modules:
        logger.warning("⚠️ Нет помещений с модулями для заполнения")
        wb.save(output_path)
        return output_path

    # Для каждого помещения создаём копию листа-шаблона
    unfilled_items = []  # собираем позиции, для которых не нашлась строка
    # P2-18: спецификация проектная; применяем к подходящим комнатам, но
    # каждую позицию — ровно один раз.
    applied_spec_items: set[str] = set()

    for i, room in enumerate(rooms_with_modules):
        sheet_name = _clean_sheet_name(room.room_name)[:31]

        # Копируем лист-шаблон
        new_ws = wb.copy_worksheet(template_ws)
        new_ws.title = sheet_name

        # Обновляем заголовок (A1) — заменяем на название помещения
        _update_title(new_ws, room, pipeline_result)

        # Блок «🧩 Мебель:» в шапке листа (строка 2) — видно, ЧТО посчитано
        _add_furniture_header(new_ws, room)

        # Рассчитываем количества (spec подавляет автодобавление)
        q = calculate_quantities(room.modules, room.room_name, room.materials, zone_type=room.zone_type,
                                 has_spec=(project_spec is not None))

        # Применяем спецификацию проекта к каждой комнате, но каждую
        # позицию — ровно один раз (P2-18).
        if project_spec:
            n_added = apply_spec_to_quantities(
                project_spec, q, room.room_name, applied_spec_items
            )
            # Если spec добавил GOLA — убираем ручки
            if n_added > 0 and (q.gola_horizontal_pcs > 0 or q.gola_vertical_pcs > 0):
                q.handles_count = 0

        # Строим мапу quantities
        qty_map = _build_quantity_map(q, room.materials, room.room_name)

        # Заполняем колонку E (Количество) в найденных строках.
        # P0-06/P2-17: одно поле — не более одной строки; одна строка — не более
        # одного значения. Иначе дубли маппинга писали объём в 5 строк.
        filled_rows = []
        room_unfilled: list[dict] = []
        room_missing_prices: list[dict] = []  # строка есть, но цены в прайсе нет
        written_fields: set[str] = set()
        written_row_numbers: set[int] = set()
        for keywords, field, unit, multiplier in ROW_MAPPING:
            value = qty_map.get(field)
            if value is not None and value > 0:
                if field in written_fields:
                    logger.warning(
                        "Дубль маппинга: поле %s уже записано, строка %s пропущена",
                        field,
                        " + ".join(keywords[:2]),
                    )
                    continue
                row_num = _find_row_for_material(new_ws, keywords)
                if row_num and row_num in written_row_numbers:
                    logger.warning(
                        "Одна строка шаблона на два поля: R%s (%s) пропущена",
                        row_num,
                        field,
                    )
                    continue
                if row_num:
                    final_value = value * multiplier
                    written_fields.add(field)
                    written_row_numbers.add(row_num)
                    # Записываем ТОЛЬКО в колонку E (5)
                    cell = new_ws.cell(row=row_num, column=5)
                    cell.value = final_value
                    # Цена — из единого прайса. Для LED-бухты прайс хранит
                    # цену за метр, а строка шаблона — за бухту 5 м.
                    price = _resolve_row_price(
                        catalog, field, keywords, unit, price_book
                    )
                    if price is not None:
                        new_ws.cell(row=row_num, column=4).value = price
                        new_ws.cell(row=row_num, column=6).value = money_float(
                            final_value * price
                        )
                    else:
                        # День 2: цена шаблона не подставляется молча.
                        new_ws.cell(row=row_num, column=4).value = None
                        new_ws.cell(row=row_num, column=6).value = 0
                        room_missing_prices.append({
                            "room": sheet_name,
                            "material": " + ".join(keywords[:2]),
                            "expected_value": f"{final_value} {unit}",
                            "field": field,
                            "problem": "цена не найдена в прайсе",
                        })
                    filled_rows.append((row_num, keywords[0], final_value, unit))
                    logger.debug(
                        f"  {sheet_name}: R{row_num} «{keywords[0][:40]}...» → "
                        f"{final_value} {unit}"
                    )
                else:
                    # Строка не найдена — попробуем добавить в «ПРОЧЕЕ» с
                    # ценой из прайса (P1-18), иначе оставим в «Проблемах».
                    room_unfilled.append({
                        "room": sheet_name,
                        "material": " + ".join(keywords[:2]),
                        "expected_value": f"{value * multiplier} {unit}",
                        "field": field,
                        "keywords": list(keywords),
                    })
                    logger.debug(
                        f"  {sheet_name}: ⚠ не найдена строка для {keywords}"
                    )

        added_extra = _append_unfilled_rows(
            new_ws, room_unfilled, catalog, price_book
        )
        if added_extra:
            logger.info(
                "  %s: %d ненайденных позиций добавлены в «ПРОЧЕЕ»",
                sheet_name,
                len(added_extra),
            )
        unfilled_items.extend(room_unfilled)
        unfilled_items.extend(room_missing_prices)

        logger.info(
            f"✅ {sheet_name}: заполнено {len(filled_rows)} строк, "
            f"модулей={len(room.modules)}, "
            f"ЛДСП={q.ldsp_sheets} листов, "
            f"кромка={q.edge_08_m:.0f}+{q.edge_04_m:.0f} м"
        )

        # P1-01: переписать финансовую секцию под единую политику.
        _apply_pricing_policy(new_ws, policy)

        # ── Добавляем рекомендации после финансовой секции ──
        if q.suggestions:
            _add_suggestions_section(new_ws, q)

    # ── Сводный лист ──
    if "СВОДКА" in [ws.title for ws in wb.worksheets]:
        _update_summary(wb, pipeline_result, rooms_with_modules)

    # P2-16: Excel пересчитает формулы при открытии (кэш значений не пишем —
    # openpyxl этого не умеет, а data_only=True до пересчёта покажет None).
    try:
        wb.calculation.fullCalcOnLoad = True
    except Exception:
        logger.debug("Не удалось включить fullCalcOnLoad", exc_info=True)

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


def _field_keywords() -> dict[str, list[str]]:
    """{поле шаблона: ключевые слова} из row_mapping.json (первое вхождение)."""
    mapping: dict[str, list[str]] = {}
    for keywords, field, _unit, _multiplier in ROW_MAPPING:
        mapping.setdefault(field, list(keywords))
    return mapping


def _clean_quantity(quantity: float, unit: str) -> float | int:
    """Количество для ячейки: м²/м — 3 знака, штуки — целыми (без 0,1 м²!)."""
    value = float(quantity or 0)
    if unit.strip().lower() in {"м²", "м2", "м"}:
        return round(value, 3)
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return int(rounded)
    return round(value, 3)


def _insert_rows_with_merges(ws, row_index: int, amount: int) -> None:
    """`insert_rows` с переносом объединённых ячеек (openpyxl их не сдвигает).

    Без этого новые строки попадали внутрь merge `A346:F346` и значения
    молча не записывались (ячейка MergedCell только для чтения).
    """
    ranges: list[tuple[int, int, int, int]] = []
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row >= row_index:
            ranges.append(
                (merged.min_row, merged.min_col, merged.max_row, merged.max_col)
            )
            ws.unmerge_cells(str(merged))
    ws.insert_rows(row_index, amount=amount)
    for min_row, min_col, max_row, max_col in ranges:
        ws.merge_cells(
            start_row=min_row + amount,
            start_column=min_col,
            end_row=max_row + amount,
            end_column=max_col,
        )


def _append_quote_extra_rows(ws, lines: list[dict]) -> None:
    """P0-02: строки, которых нет в шаблоне, — в блок «ПРОЧЕЕ» перед сметой.

    Значения берутся из снимка (`quote.lines`), а не пересчитываются.
    """
    if not lines:
        return

    summary_row = None
    for row in range(1, min(ws.max_row, 800) + 1):
        value = str(ws.cell(row=row, column=1).value or "").strip().upper()
        if value.startswith("РАСЧЁТ СТОИМОСТИ"):
            summary_row = row
            break
    if summary_row is None:
        summary_row = ws.max_row + 1

    insert_at = max(2, summary_row - 1)
    _insert_rows_with_merges(ws, insert_at, len(lines) + 1)
    ws.cell(insert_at, 1).value = "ПРОЧЕЕ (из снимка расчёта)"
    for offset, line in enumerate(lines, start=1):
        row = insert_at + offset
        ws.cell(row=row, column=1).value = line.get("name") or "Прочее"
        ws.cell(row=row, column=4).value = float(line.get("unit_price") or 0)
        ws.cell(row=row, column=5).value = _clean_quantity(
            line.get("quantity") or 0, str(line.get("unit") or "")
        )
        ws.cell(row=row, column=6).value = money_float(line.get("amount") or 0)


def fill_template_from_quote(
    quote: dict,
    template_path: str,
    output_path: str,
    *,
    sheet_name: str | None = None,
) -> str:
    """P0-02: заполнить Excel из неизменяемого снимка `quote`.

    В отличие от `fill_template_from_pipeline`, здесь НЕТ второго расчёта:
    - количества и суммы строк — из `quote["lines"]` (с поле `field` из
      `build_cost_lines`, см. `templates/row_mapping.json`);
    - финансовая секция — из `quote["totals"]` (те же числа, что чат/КП);
    - позиции без строки в шаблоне (стекло, BLUM/HETTICH, AGT/EVOGLOSS…)
      попадают в блок «ПРОЧЕЕ» с ценой и суммой из снимка, а не в молчаливый
      ноль и не в чужую строку FIRMAX/ЛЕМАКОМ.

    Критерий паритета: сумма строк = `cost_price`, итог = `final_cash`.
    """
    from openpyxl import load_workbook

    if not quote or not quote.get("lines"):
        raise ValueError("Снимок расчёта пуст: нечего заполнять в Excel")
    totals = quote.get("totals") or {}
    if not totals:
        raise ValueError("В снимке нет финансовой разбивки (totals)")

    wb = load_workbook(template_path)
    if "Рассчет" not in wb.sheetnames:
        raise ValueError("В шаблоне нет листа «Рассчет»")
    ws = wb["Рассчет"]
    for name in list(wb.sheetnames):
        if name != "Рассчет":
            wb.remove(wb[name])

    if sheet_name:
        ws.title = _clean_sheet_name(sheet_name)[:31]

    field_rows = _field_keywords()
    used_rows: set[int] = set()
    extras: list[dict] = []

    for line in quote["lines"]:
        quantity = float(line.get("quantity") or 0)
        if quantity <= 0:
            continue
        row = None
        field = line.get("field")
        if field:
            keywords = field_rows.get(str(field))
            if keywords:
                candidate = _find_row_for_material(ws, keywords)
                if candidate and candidate not in used_rows:
                    row = candidate
        if row:
            used_rows.add(row)
            ws.cell(row=row, column=4).value = float(line.get("unit_price") or 0)
            ws.cell(row=row, column=5).value = _clean_quantity(
                quantity, str(line.get("unit") or "")
            )
            ws.cell(row=row, column=6).value = money_float(line.get("amount") or 0)
        else:
            extras.append(line)

    _append_quote_extra_rows(ws, extras)

    # ── Финансовая секция: значения из снимка, не пересчёт ──
    manufacturing_row = _find_row_by_label(ws, "ИЗГОТОВЛЕНИЕ")
    profit_row = _find_row_by_label(ws, "ПЛАНИРУЕМАЯ ПРИБЫЛЬ")

    def put(label: str, value: float | None, start: int = 1) -> None:
        row = _find_row_by_label(ws, label, start=start)
        if row is not None:
            ws.cell(row=row, column=5).value = money_float(float(value or 0))

    put("СЕБЕСТОИМОСТЬ", totals.get("cost_price"))
    put("ИЗГОТОВЛЕНИЕ", totals.get("manufacturing"))
    put("МОНТАЖ", totals.get("installation"))
    put("ЗАМЕР", totals.get("measurement"))
    put("ДОСТАВКА", totals.get("delivery_client"))
    put("ПРОЕКТИРОВКА", totals.get("design"))
    put("ПРОЧИЕ РАСХОДЫ", totals.get("overhead"))

    subtotal_row = _find_row_by_label(
        ws, "ИТОГО:", start=(manufacturing_row or 1)
    )
    if subtotal_row:
        ws.cell(row=subtotal_row, column=5).value = rub_float(
            float(totals.get("subtotal") or 0)
        )
    put("ПЛАНИРУЕМАЯ ПРИБЫЛЬ", totals.get("profit"))
    total_row = _find_row_by_label(ws, "ИТОГО:", start=(profit_row or 1) + 1)
    if total_row:
        ws.cell(row=total_row, column=5).value = rub_float(
            float(totals.get("total_with_profit") or 0)
        )
    put("ТЕХНИЧЕСКИЙ ДИРЕКТОР", totals.get("tech_director"))
    put("МЕНЕДЖЕР", totals.get("manager_salary"))
    put("ИТОГО за наличку", totals.get("final_cash"))
    put("безнал", totals.get("final_noncash"))

    # Формулы шаблона в неиспользуемых строках пусть пересчитает Excel.
    try:
        wb.calculation.fullCalcOnLoad = True
    except Exception:
        logger.debug("Не удалось включить fullCalcOnLoad", exc_info=True)

    wb.save(output_path)
    logger.info(
        "💾 Excel из снимка: %s (строк: %s, себестоимость: %s, итог: %s)",
        output_path,
        len(quote["lines"]),
        totals.get("cost_price"),
        totals.get("final_cash"),
    )
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
    from openpyxl.styles import Alignment, Font, PatternFill

    sheet_name = "✅ Контроль качества"

    # Удаляем старый лист если есть (при повторном запуске)
    if sheet_name in [ws.title for ws in wb.worksheets]:
        del wb[sheet_name]

    # P2-15: QC — служебный лист, в конец книги, чтобы не открывался первым.
    ws = wb.create_sheet(sheet_name)

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


def _module_ru_name(mtype: str | None) -> str:
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
    ws["B1"] = "Материал / причина"
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
        action = (
            "Добавлено в блок «ПРОЧЕЕ» (цена из прайса)"
            if item.get("added_to_extra")
            else item.get("problem", "Цена не найдена — внести вручную")
        )
        ws.cell(row=i, column=4, value=action)

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


def _add_furniture_header(ws, room: RoomSpec):
    """
    Блок «🧩 Мебель:» в шапке листа-комнаты (строка 2, мерж A2:H2).

    Оператор сразу видит, КАКАЯ мебель распознана и посчитана в этом листе:
      • Нижняя база 634×560×716 мм — 6 шт (1 двер., 2 ящика)
      • Верхняя база 634×320×596 мм — 6 шт (стекло)
    Строка 2 в копии шаблона пустая, таблица цен начинается со строки 3 —
    поэтому блок не сдвигает и не ломает раскладку/мержи шаблона.
    """
    from openpyxl.styles import Alignment, Font, PatternFill

    modules = room.modules or []
    if not modules:
        return

    lines = []
    for m in modules:
        qty = getattr(m, "quantity", 1) or 1
        detail = []
        facades = (m.facades or {}).get("count") if isinstance(m.facades, dict) else None
        drawers = (m.drawers or {}).get("count") if isinstance(m.drawers, dict) else None
        if facades:
            detail.append(f"{facades} двер." if facades != 1 else "1 двер.")
        if drawers:
            detail.append(f"ящиков {drawers}" if drawers != 1 else f"{drawers} ящик")
        if getattr(m, "has_glass", False):
            detail.append("стекло")
        if getattr(m, "is_corner", False):
            detail.append("угловой")
        name = _module_ru_name(getattr(m, "type", ""))
        dims = f"{getattr(m, 'width', 0)}×{getattr(m, 'depth', 0)}×{getattr(m, 'height', 0)}"
        line = f"{name} {dims} мм — {qty} шт"
        if detail:
            line += " (" + ", ".join(detail) + ")"
        lines.append(line)

    text = "🧩 Мебель:\n" + "\n".join(f"  • {line}" for line in lines)

    # Вторую строку не используем под адрес — помещаем состав мебели.
    # Мержим широко (A:H), чтобы строки помещались без обрезки.
    range_ = "A2:H2"
    if range_ not in [str(r) for r in ws.merged_cells.ranges]:
        ws.merge_cells(range_)
    cell = ws["A2"]
    # P2-15: не затираем адрес объекта, который записал _update_title.
    existing = str(cell.value or "").strip()
    cell.value = f"{existing}\n{text}" if existing else text
    cell.font = Font(name="Arial", size=9)
    cell.fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    # Высота строки = под каждую строку списка
    ws.row_dimensions[2].height = 14 * (len(lines) + 1) + 4

    logger.info(f"🧩 {room.room_name}: шапка «Мебель:» — {len(lines)} модулей")


def _resolve_row_price(
    catalog: list[PriceCatalogItem],
    field: str,
    keywords: list[str],
    unit: str,
    price_book: dict[str, float] | None = None,
) -> float | None:
    """Цена строки шаблона из единого прайса (День 2/4).

    Для позиций, которые чат берёт из `load_price_book` (ХДФ, столешница,
    крепёж, GOLA, LED, ручки), Excel использует тот же словарь — иначе
    чат и Excel расходятся. Остальные строки подбираются по каталогу.
    LED-бухта: в прайсе цена за метр, в шаблоне — за бухту 5 м.
    """
    book = price_book or {}
    key = _PRICE_BOOK_FIELD.get(field)
    if key and key in book:
        price = float(book[key])
        if field == "led_strip_5m":
            return round(price * 5, 2)
        return price

    if field == "led_strip_5m":
        match = find_price(catalog, keywords, "м")
        return round(match.item.price * 5, 2) if match else None
    match = find_price(catalog, keywords, unit)
    return match.item.price if match else None


# Поля шаблона, которые чат тарифицирует через `load_price_book`.
_PRICE_BOOK_FIELD = {
    "hdf_sheets": "hdf",
    "gola_horizontal_3m": "gola_horizontal",
    "gola_vertical_3m": "gola_vertical",
    "led_strip_5m": "led_strip",
    "led_power_supply": "led_power",
    "led_sensor": "led_sensor",
    "countertop_length_m": "countertop",
    "confirmat_count": "confirmat",
    "adjustable_feet": "adjustable_feet",
    "wall_mounts": "wall_mount",
    "plinth_strips": "plinth",
    "handles_count": "handles",
}


def _parse_expected_quantity(text: str) -> tuple[float, str]:
    """«5 м²» → (5.0, "м²"); «18 шт» → (18.0, "шт")."""
    import re

    match = re.match(r"\s*([\d]+(?:[.,]\d+)?)\s*(.*)$", str(text or ""))
    if not match:
        return 0.0, ""
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return 0.0, ""
    return value, match.group(2).strip()


def _append_unfilled_rows(
    ws,
    items: list[dict],
    catalog: list[PriceCatalogItem],
    price_book: dict[str, float] | None = None,
) -> list[dict]:
    """P1-18: ненайденные строки → блок «ПРОЧЕЕ» с ценой из прайса.

    Блок вставляется ПЕРЕД финансовой секцией, поэтому попадает в
    `СЕБЕСТОИМОСТЬ` и дальше во все коэффициенты. Позиции без цены в прайсе
    остаются в листе «⚠ Проблемы».
    """
    priced = []
    for item in items:
        quantity, unit = _parse_expected_quantity(item.get("expected_value", ""))
        if quantity <= 0:
            continue
        price = _resolve_row_price(
            catalog,
            str(item.get("field") or ""),
            item.get("keywords", []),
            unit,
            price_book,
        )
        if price is None:
            continue
        priced.append((item, quantity, unit, price))

    if not priced:
        return []

    summary_row = None
    for row in range(1, min(ws.max_row, 500) + 1):
        value = str(ws.cell(row=row, column=1).value or "").strip().upper()
        if value.startswith("РАСЧЁТ СТОИМОСТИ"):
            summary_row = row
            break
    if summary_row is None:
        return []

    # Вставляем ПЕРЕД заголовком «РАСЧЁТ СТОИМОСТИ». Если вставить ровно в
    # строку заголовка, openpyxl сдвинет его merge (A:F) на вставленные строки,
    # и ячейки D/E окажутся MergedCell.
    insert_at = summary_row - 1
    ws.insert_rows(insert_at, amount=len(priced) + 1)
    ws.cell(insert_at, 1).value = "ПРОЧЕЕ (позиции из прайса)"
    added: list[dict] = []
    for offset, (item, quantity, _unit, price) in enumerate(priced, start=1):
        row = insert_at + offset
        ws.cell(row=row, column=1).value = item.get("material", "Прочее")
        ws.cell(row=row, column=4).value = price
        ws.cell(row=row, column=5).value = quantity
        ws.cell(row=row, column=6).value = money_float(quantity * price)
        item["added_to_extra"] = True
        added.append(item)

    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    if cost_row:
        ws.cell(cost_row, 5).value = money_float(
            sum(_cell_number(ws, row, 6) for row in range(3, cost_row))
        )
    return added


def _cell_number(ws, row: int, column: int) -> float:
    value = ws.cell(row=row, column=column).value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _find_row_by_label(ws, label: str, start: int = 1) -> int | None:
    """Найти строку финансовой секции по подписи в колонке D."""
    for row in range(start, min(ws.max_row, 400) + 1):
        value = ws.cell(row=row, column=4).value
        if value is not None and str(value).strip().lower().startswith(label.lower()):
            return row
    return None


def _apply_pricing_policy(ws, policy) -> bool:
    """Переписать финансовую секцию листа по единой политике (P1-01).

    Возвращает False, если секция не найдена (не ломаем нестандартный шаблон).
    """
    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    manufacturing_row = _find_row_by_label(ws, "ИЗГОТОВЛЕНИЕ")
    installation_row = _find_row_by_label(ws, "МОНТАЖ")
    measurement_row = _find_row_by_label(ws, "ЗАМЕР")
    delivery_row = _find_row_by_label(ws, "ДОСТАВКА")
    design_row = _find_row_by_label(ws, "ПРОЕКТИРОВКА")
    overhead_row = _find_row_by_label(ws, "ПРОЧИЕ РАСХОДЫ")
    profit_row = _find_row_by_label(ws, "ПЛАНИРУЕМАЯ ПРИБЫЛЬ")
    tech_row = _find_row_by_label(ws, "ТЕХНИЧЕСКИЙ ДИРЕКТОР")
    manager_row = _find_row_by_label(ws, "МЕНЕДЖЕР")
    cash_row = _find_row_by_label(ws, "ИТОГО за наличку")
    noncash_row = _find_row_by_label(ws, "безнал")

    if not all(
        (cost_row, manufacturing_row, installation_row, measurement_row,
         delivery_row, design_row, overhead_row, profit_row, tech_row,
         manager_row, cash_row, noncash_row)
    ):
        logger.warning("Финансовая секция шаблона не найдена — политика не применена")
        return False

    # Первое «ИТОГО:» до прибыли — промежуточный итог, после прибыли — итог с прибылью.
    subtotal_row = _find_row_by_label(ws, "ИТОГО:", start=manufacturing_row)
    total_row = _find_row_by_label(ws, "ИТОГО:", start=profit_row + 1)
    if subtotal_row is None or total_row is None or subtotal_row >= profit_row:
        logger.warning("Строки «ИТОГО» шаблона не найдены — политика не применена")
        return False

    # P2-16: пишем вычисленные значения, а не формулы. Excel-файл больше не
    # зависит от пересчёта при открытии, а openpyxl data_only видит цифры.
    cost = money_float(sum(_cell_number(ws, row, 6) for row in range(3, cost_row)))
    manufacturing = rub_float(cost * policy.manufacturing_rate)
    installation = rub_float(cost * policy.installation_rate)
    measurement = rub_float(policy.measurement_fee)
    # День 4: клиенту — 10 000 ₽ отдельной строкой; внутренняя доставка
    # (policy.delivery_fee) в расчётную базу и наценку не входит.
    delivery = rub_float(policy.delivery_fee_client)
    design = rub_float(manufacturing * policy.design_rate)
    overhead = rub_float(cost * policy.overhead_rate)
    # Не округляем промежуточный итог до прибыли: чат считает прибыль от
    # неокруглённой суммы, иначе Excel расходится на 1–2 ₽ (День 4).
    subtotal = (
        cost + manufacturing + installation + measurement + design + overhead
    )
    profit = rub_float(subtotal * policy.profit_rate)
    total_with_profit = subtotal + profit
    tech = rub_float(total_with_profit * policy.tech_director_rate)
    manager = rub_float(total_with_profit * policy.manager_rate)
    furniture_cash = total_with_profit + tech + manager
    cash = money_float(furniture_cash + delivery)
    noncash = money_float(furniture_cash * policy.noncash_markup + delivery)

    # В ячейки пишем округлённые значения (деньги — до копеек/рубля).
    subtotal_cell = rub_float(subtotal)
    total_with_profit_cell = rub_float(total_with_profit)

    for row, value in (
        (cost_row, cost),
        (manufacturing_row, manufacturing),
        (installation_row, installation),
        (measurement_row, measurement),
        (delivery_row, delivery),
        (design_row, design),
        (overhead_row, overhead),
        (subtotal_row, subtotal_cell),
        (profit_row, profit),
        (total_row, total_with_profit_cell),
        (tech_row, tech),
        (manager_row, manager),
        (cash_row, cash),
        (noncash_row, noncash),
    ):
        ws.cell(row=row, column=5).value = value
    return True


def _update_summary(wb: Workbook, result: PipelineResult, rooms: list[RoomSpec]):
    """Обновить сводный лист (если есть)."""
    if "СВОДКА" not in [ws.title for ws in wb.worksheets]:
        return


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
    spec_path: str | None = None,
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
    from app.services.full_pipeline import print_result, run_pipeline

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

    # Шаг 2: Заполнение шаблона (цены — из единого прайса БД, как в чате).
    print()
    print("📊 Заполнение шаблона...")
    from app.db.session import AsyncSessionLocal
    from app.services.price_book import load_price_book
    from app.services.price_catalog import load_price_catalog

    async with AsyncSessionLocal() as session:
        price_catalog = await load_price_catalog(session)
        price_book = await load_price_book(session)

    path = fill_template_from_pipeline(
        pipeline_result=result,
        template_path=template_path,
        output_path=output_path,
        project_spec=project_spec,
        price_catalog=price_catalog,
        price_book=price_book,
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
