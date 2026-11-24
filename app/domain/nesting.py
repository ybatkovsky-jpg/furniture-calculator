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


def nest_parts(
    parts: list,
    sheet_sizes: dict[str, tuple[int, int]] | None = None,
    *,
    grain_locked: bool = False,
) -> NestingResult:
    """Разложить физические детали (`Part`) по листам.

    В отличие от `nest_bom`, здесь количество уже учтено в `Part.quantity`,
    а направление текстуры задаётся самим Part: `grain_direction` запрещает
    поворот только этой детали. `grain_locked=True` запрещает поворот всем
    деталям (текстурный ЛДСП на весь проект).
    """
    sizes = dict(SHEET_SIZES)
    if sheet_sizes:
        sizes.update(sheet_sizes)

    parts_by_material: dict[str, list] = {}
    for part in parts or []:
        raw_material = getattr(part, "material", "")
        material = getattr(part, "material_group", None) or raw_material
        if material not in sizes:
            from app.domain.parts import material_group

            material = material_group(str(raw_material or ""))
        if material not in sizes:
            continue
        parts_by_material.setdefault(material, []).append(part)

    result = NestingResult()
    for material, material_parts in parts_by_material.items():
        sheet_w, sheet_h = sizes[material]
        expanded: list = []
        for part in material_parts:
            expanded.extend(
                [part] * max(1, int(getattr(part, "quantity", 1) or 1))
            )
        expanded.sort(
            key=lambda part: (part.height_mm, part.width_mm),
            reverse=True,
        )

        sheets: list[SheetLayout] = []
        for part in expanded:
            placed = False
            locked = grain_locked or bool(
                getattr(part, "grain_direction", None)
            )
            variants = [(part.width_mm, part.height_mm, False)]
            if part.width_mm != part.height_mm and not locked:
                variants.append((part.height_mm, part.width_mm, True))
            for variant_w, variant_h, rotated in variants:
                placed_part = PlacedPart(
                    part.name, variant_w, variant_h, 0, 0, rotated
                )
                for sheet in sheets:
                    if _try_place(sheet, placed_part):
                        placed = True
                        break
                if placed:
                    break
                if variant_w <= sheet_w and variant_h <= sheet_h:
                    new_sheet = SheetLayout(
                        material, len(sheets) + 1, sheet_w, sheet_h
                    )
                    if _try_place(new_sheet, placed_part):
                        sheets.append(new_sheet)
                        placed = True
                        break
            if not placed:
                result.unplaced.append(
                    {
                        "material": material,
                        "part": part.name,
                        "width_mm": part.width_mm,
                        "height_mm": part.height_mm,
                    }
                )
        result.layouts.extend(sheets)
    return result


def nest_parts_guillotine(
    parts: list,
    sheet_sizes: dict[str, tuple[int, int]] | None = None,
    *,
    grain_locked: bool = False,
) -> NestingResult:
    """Рабочий раскрой: Guillotine по физическим деталям.

    Поворот разрешён только нетекстурированным деталям: если у детали задано
    `grain_direction` или включён `grain_locked`, она кладётся только как есть.
    """
    from app.domain.nesting_algorithms import Rect, pack_guillotine, pack_multi

    sizes = dict(SHEET_SIZES)
    if sheet_sizes:
        sizes.update(sheet_sizes)

    parts_by_material: dict[str, list] = {}
    for part in parts or []:
        raw_material = getattr(part, "material", "")
        material = getattr(part, "material_group", None) or raw_material
        if material not in sizes:
            from app.domain.parts import material_group

            material = material_group(str(raw_material or ""))
        if material not in sizes:
            continue
        parts_by_material.setdefault(material, []).append(part)

    result = NestingResult()
    for material, material_parts in parts_by_material.items():
        sheet_w, sheet_h = sizes[material]
        rects = []
        for part in material_parts:
            rotatable = not (
                grain_locked or bool(getattr(part, "grain_direction", None))
            )
            for _ in range(max(1, int(getattr(part, "quantity", 1) or 1))):
                rects.append(
                    Rect(
                        name=str(getattr(part, "name", "деталь")),
                        width_mm=int(part.width_mm),
                        height_mm=int(part.height_mm),
                        rotatable=rotatable,
                    )
                )
        rects.sort(key=lambda r: (r.height_mm, r.width_mm), reverse=True)
        sheets, unplaced = pack_multi(
            pack_guillotine, rects, sheet_w, sheet_h, SAW_KERF_MM
        )

        for sheet_index, placements in enumerate(sheets, start=1):
            layout = SheetLayout(material, sheet_index, sheet_w, sheet_h)
            layout.parts = [
                PlacedPart(
                    name=item.name,
                    width_mm=item.width_mm,
                    height_mm=item.height_mm,
                    x_mm=item.x_mm,
                    y_mm=item.y_mm,
                    rotated=item.rotated,
                )
                for item in placements
            ]
            result.layouts.append(layout)

        for rect in unplaced:
            result.unplaced.append(
                {
                    "material": material,
                    "part": rect.name,
                    "width_mm": rect.width_mm,
                    "height_mm": rect.height_mm,
                }
            )
    return result


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
