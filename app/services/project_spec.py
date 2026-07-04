"""
Загрузка и применение спецификации проекта (project_spec.yaml).

Оператор заполняет YAML один раз — калькулятор подхватывает позиции,
которые AI не может определить по чертежу (холодильник, спец. ящики, LED, etc.).

Позиции из спеки добавляются ПОВЕРХ AI-расчёта. Дубли не создаются.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None
    logger.warning("PyYAML не установлен. pip install pyyaml")


@dataclass
class SpecItem:
    """Одна позиция из спецификации проекта."""
    name: str
    count: float = 1
    unit: str = "шт"
    category: str = "Дополнительно"
    brand: str = ""
    model: str = ""
    notes: str = ""


@dataclass
class ProjectSpec:
    """Загруженная спецификация проекта."""
    project_name: str = ""
    project_address: str = ""
    items: List[SpecItem] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return len(self.items)


def load_project_spec(spec_path: str | Path) -> Optional[ProjectSpec]:
    """
    Загрузить спецификацию проекта из YAML.

    Args:
        spec_path: путь к project_spec.yaml

    Returns:
        ProjectSpec или None если файл не найден / ошибка парсинга
    """
    if yaml is None:
        logger.error("PyYAML не установлен — спецификация не загружена")
        return None

    path = Path(spec_path)
    if not path.exists():
        logger.info(f"📋 Спецификация не найдена: {path} (пропускаем)")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения YAML: {e}")
        return None

    if not data:
        return None

    spec = ProjectSpec()

    # Метаданные проекта
    project = data.get("project", {})
    spec.project_name = project.get("name", "")
    spec.project_address = project.get("address", "")

    # Парсим все секции (kitchen, wardrobe, bathroom, ...)
    for section_name, section_data in data.items():
        if section_name == "project":
            continue
        if not isinstance(section_data, dict):
            continue
        _parse_section(spec, section_data, section_name)

    logger.info(f"📋 Спецификация загружена: {len(spec.items)} позиций из {path.name}")
    return spec


def _parse_section(spec: ProjectSpec, data: dict, room: str):
    """Рекурсивно извлечь позиции из секции YAML."""

    # Холодильник
    fridge = data.get("refrigerator")
    if isinstance(fridge, dict):
        hinges = fridge.get("hinges", {})
        if isinstance(hinges, dict) and hinges.get("count", 0) > 0:
            spec.items.append(SpecItem(
                name=f"Петли {hinges.get('brand', '')} для холодильника",
                count=hinges["count"],
                unit="шт",
                category="Петли",
                brand=hinges.get("brand", ""),
                notes=f"Встраиваемый холодильник ({room})",
            ))

    # Ящики
    drawers = data.get("drawers")
    if isinstance(drawers, dict):
        # Внутренний ящик
        internal = drawers.get("internal")
        if isinstance(internal, dict) and internal.get("count", 0) > 0:
            spec.items.append(SpecItem(
                name=f"Ящик внутренний {internal.get('model', '')}",
                count=internal["count"],
                unit="шт",
                category="Ящики",
                notes=f"Внутренний, для столовых приборов ({room})",
            ))

        # Фасадные ящики
        facades = drawers.get("facades", [])
        for f in (facades if isinstance(facades, list) else []):
            if isinstance(f, dict):
                spec.items.append(SpecItem(
                    name=f"Ящик {f.get('brand', '')} {f.get('model', '')} "
                         f"{f.get('width_mm', '')}мм h={f.get('height_mm', '')}мм",
                    count=f.get("count", 1),
                    unit="шт",
                    category="Ящики",
                    brand=f.get("brand", ""),
                    model=f.get("model", ""),
                    notes=f"Фасадный ящик ({room})",
                ))

        # LED подсветка ящиков
        led = drawers.get("led")
        if isinstance(led, dict) and led.get("count", 0) > 0:
            spec.items.append(SpecItem(
                name=f"LED-подсветка {led.get('brand', '')} {led.get('model', '')}",
                count=led["count"],
                unit="шт",
                category="Подсветка",
                notes=f"Для ящиков ({room})",
            ))
            bat = led.get("batteries_aaa", 0)
            if bat > 0:
                spec.items.append(SpecItem(
                    name="Батарейки AAA",
                    count=bat,
                    unit="шт",
                    category="Подсветка",
                    notes=f"Для LED ящиков ({room})",
                ))

        # Направляющие
        guides = drawers.get("guides")
        if isinstance(guides, dict) and guides.get("count", 0) > 0:
            spec.items.append(SpecItem(
                name=f"Направляющие {guides.get('brand', '')} {guides.get('model', '')} "
                     f"L={guides.get('length_mm', '')}мм",
                count=guides["count"],
                unit="шт",
                category="Фурнитура",
                brand=guides.get("brand", ""),
                notes=f"Скрытого монтажа ({room})",
            ))

    # Дополнительные позиции
    additional = data.get("additional", [])
    for item in (additional if isinstance(additional, list) else []):
        if isinstance(item, dict):
            spec.items.append(SpecItem(
                name=item.get("name", ""),
                count=item.get("count", 1),
                unit=item.get("unit", "шт"),
                category="Дополнительно",
                notes=room,
            ))


def apply_spec_to_quantities(
    spec: Optional[ProjectSpec],
    materials_qty,  # MaterialQuantities
    room_name: str = "",
) -> int:
    """
    Применить позиции из спецификации к MaterialQuantities.
    Добавляет только то, чего ещё нет в расчёте.

    Returns:
        количество добавленных позиций
    """
    if spec is None or not spec.items:
        return 0

    added = 0
    room_lower = room_name.lower()
    is_kitchen = any(kw in room_lower for kw in ["кухн", "остров", "гарнитур", "kitchen"])

    for item in spec.items:
        notes_lower = (item.notes or "").lower()

        # Фильтр по комнате: применяем только если комната совпадает
        if "кухн" in notes_lower or "kitchen" in notes_lower or "остров" in notes_lower:
            if not is_kitchen:
                continue

        # Применяем к quantities
        if "петли" in item.name.lower() and "холодильник" in item.name.lower():
            if materials_qty.hinges_count == 0:
                materials_qty.hinges_count += int(item.count)
                added += 1
        elif "внутренний" in item.name.lower() and "ящик" in item.name.lower():
            if materials_qty.drawers_internal_count == 0:
                materials_qty.drawers_internal_count += int(item.count)
                added += 1
        elif "ящик" in item.name.lower():
            materials_qty.drawers_count += int(item.count)
            added += 1
        elif "led" in item.name.lower() or "подсветк" in item.name.lower():
            materials_qty.led_strip_m += 5.0  # бухта 5м
            materials_qty.led_power_supply += 1
            materials_qty.led_sensor += 1
            added += 1
        elif "направляющие" in item.name.lower():
            # Добавляем как доп. фурнитуру — не меняем основные счётчики
            added += 1
        elif "бутылочниц" in item.name.lower():
            materials_qty.bottle_holder_count += int(item.count)
            added += 1
        elif "сушка" in item.name.lower():
            materials_qty.drying_rack_count += int(item.count)
            added += 1
        elif "лоток" in item.name.lower():
            materials_qty.cutlery_tray_count += int(item.count)
            added += 1
        elif "цоколь" in item.name.lower():
            added += 1  # Учтено в accessory items
        elif "gola" in item.name.lower():
            if "горизонт" in item.name.lower() and "g" not in item.name.lower():
                materials_qty.gola_horizontal_pcs += int(item.count)
            elif "вертикал" in item.name.lower():
                materials_qty.gola_vertical_pcs += int(item.count)
            else:
                # G-образный — как горизонтальный
                materials_qty.gola_horizontal_pcs += int(item.count)
            added += 1
        elif "батарейк" in item.name.lower():
            added += 1  # Информационная позиция
        elif "гигиенический" in item.name.lower() or "поддон" in item.name.lower():
            added += 1

    if added > 0:
        logger.info(f"📋 Спецификация: +{added} позиций для «{room_name}»")

    return added


def print_spec_summary(spec: Optional[ProjectSpec]):
    """Вывести сводку загруженной спецификации."""
    if spec is None or not spec.items:
        print("📋 Спецификация проекта: НЕ ЗАГРУЖЕНА")
        return

    print(f"📋 Спецификация проекта: {spec.project_name or '(без названия)'}")
    if spec.project_address:
        print(f"   📍 {spec.project_address}")
    print(f"   Позиций: {len(spec.items)}")
    print()

    by_cat = {}
    for item in spec.items:
        by_cat.setdefault(item.category, []).append(item)

    for cat, items in by_cat.items():
        print(f"   {cat}:")
        for item in items:
            print(f"      • {item.name} ×{item.count} {item.unit}")
    print()
