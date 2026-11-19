"""Подготовка к раскрою (S5-03): простая укладка деталей на листы.

Это заготовка, а не финальный nesting: алгоритм «полочный» (shelf packing),
без оптимизации по текстуре/направлению волокон. Он нужен, чтобы видеть
количество листов и процент отхода по BOM, не меняя текущую смету.

Единицы: миллиметры. Учитывается пропил SAW_KERF_MM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SAW_KERF_MM = 4

SHEET_SIZES: dict[str, tuple[int, int]] = {
    "ЛДСП": (2800, 2070),
    "ХДФ": (2800, 1220),
    "МДФ": (2800, 1220),
    "Стекло": (2000, 3300),
}


@dataclass
class PlacedPart:
    name: str
    width_mm: int
    height_mm: int
    x_mm: int
    y_mm: int
    rotated: bool = False


@dataclass
class SheetLayout:
    material: str
    sheet_index: int
    width_mm: int
    height_mm: int
    parts: list[PlacedPart] = field(default_factory=list)
    _shelf_y: int = field(default=0, repr=False)
    _shelf_h: int = field(default=0, repr=False)
    _cursor_x: int = field(default=0, repr=False)

    @property
    def used_area_m2(self) -> float:
        return round(
            sum(p.width_mm * p.height_mm for p in self.parts) / 1_000_000, 4
        )


@dataclass
class NestingResult:
    layouts: list[SheetLayout] = field(default_factory=list)
    unplaced: list[dict] = field(default_factory=list)

    @property
    def sheets_count(self) -> int:
        return len(self.layouts)

    @property
    def sheet_area_m2(self) -> float:
        return round(
            sum(l.width_mm * l.height_mm for l in self.layouts) / 1_000_000, 4
        )

    @property
    def used_area_m2(self) -> float:
        return round(sum(l.used_area_m2 for l in self.layouts), 4)

    @property
    def waste_percent(self) -> float:
        if self.sheet_area_m2 <= 0:
            return 0.0
        return round((1 - self.used_area_m2 / self.sheet_area_m2) * 100, 1)


def _try_place(sheet: SheetLayout, part: PlacedPart) -> bool:
    """Полочная укладка: слева-направо, затем новая полка вниз."""
    kerf = SAW_KERF_MM
    # Высота текущей полки: максимальная высота уже положенных деталей на
    # последней полке. Храним курсор Y и высоту полки в служебных полях.
    shelf_y = getattr(sheet, "_shelf_y", 0)
    shelf_h = getattr(sheet, "_shelf_h", 0)
    cursor_x = getattr(sheet, "_cursor_x", 0)

    if cursor_x + part.width_mm <= sheet.width_mm and shelf_y + part.height_mm <= sheet.height_mm:
        part.x_mm, part.y_mm = cursor_x, shelf_y
        sheet.parts.append(part)
        sheet._cursor_x = cursor_x + part.width_mm + kerf
        sheet._shelf_h = max(shelf_h, part.height_mm)
        return True

    # Новая полка
    new_y = shelf_y + shelf_h + kerf
    if new_y + part.height_mm <= sheet.height_mm and part.width_mm <= sheet.width_mm:
        part.x_mm, part.y_mm = 0, new_y
        sheet.parts.append(part)
        sheet._shelf_y = new_y
        sheet._shelf_h = part.height_mm
        sheet._cursor_x = part.width_mm + kerf
        return True
    return False


def nest_bom(
    bom: list,
    sheet_sizes: dict[str, tuple[int, int]] | None = None,
    *,
    grain_locked: bool = False,
) -> NestingResult:
    """Разложить детали BOM по листам.

    `bom` — список ModuleBOM (из app.domain.bom). Детали с материалом, для
    которого нет размера листа, попадают в `unplaced`.

    `grain_locked=True` — направление рисунка задано (текстурный декор):
    поворот детали на 90° запрещён, деталь кладётся только так, как задана в
    BOM. Иначе поворот рвал бы рисунок на боковинах и фасадах.
    """
    sizes = dict(SHEET_SIZES)
    if sheet_sizes:
        sizes.update(sheet_sizes)

    parts_by_material: dict[str, list[tuple[str, int, int, int]]] = {}
    for module_bom in bom:
        # P2-11: `ModuleBOM.quantity` — число одинаковых корпусов. Без этого
        # множителя 5 одинаковых модулей, свёрнутых в одну запись, давали
        # детали одного корпуса и занижали листы/отход.
        module_qty = max(int(getattr(module_bom, "quantity", 1) or 1), 1)
        for line in module_bom.lines:
            material = line.material
            if material not in sizes:
                continue
            parts_by_material.setdefault(material, []).append(
                (line.part, line.width_mm, line.height_mm, line.quantity * module_qty)
            )

    result = NestingResult()
    for material, parts in parts_by_material.items():
        sheet_w, sheet_h = sizes[material]
        expanded: list[tuple[str, int, int]] = []
        for name, width, height, quantity in parts:
            expanded.extend([(name, width, height)] * max(quantity, 1))
        expanded.sort(key=lambda item: (item[2], item[1]), reverse=True)

        sheets: list[SheetLayout] = []
        for name, width, height in expanded:
            placed = False
            # Поворот на 90°, если так деталь влезает. Для направленного
            # рисунка (текстурный ЛДСП) поворот запрещён — иначе рисунок
            # встанет поперёк на части деталей.
            variants = [(width, height, False)]
            if width != height and not grain_locked:
                variants.append((height, width, True))
            for variant_w, variant_h, rotated in variants:
                part = PlacedPart(name, variant_w, variant_h, 0, 0, rotated)
                for sheet in sheets:
                    if _try_place(sheet, part):
                        placed = True
                        break
                if placed:
                    break
                if variant_w <= sheet_w and variant_h <= sheet_h:
                    new_sheet = SheetLayout(material, len(sheets) + 1, sheet_w, sheet_h)
                    if _try_place(new_sheet, part):
                        sheets.append(new_sheet)
                        placed = True
                        break
            if not placed:
                result.unplaced.append(
                    {"material": material, "part": name, "width_mm": width, "height_mm": height}
                )
        result.layouts.extend(sheets)
    return result
