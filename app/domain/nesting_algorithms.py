"""Алгоритмы раскроя для бенчмарка этапа 4.

MaxRects / Guillotine / Skyline работают с прямоугольниками в миллиметрах.
Это независимые упаковщики для сравнения на эталонных BOM; текущая смета
по-прежнему использует `nest_parts` (полочная укладка), пока бенчмарк не
покажет выигрыш и технолог не подтвердит правила.
"""

from __future__ import annotations

from dataclasses import dataclass

SAW_KERF_MM = 4


@dataclass(frozen=True)
class Rect:
    name: str
    width_mm: int
    height_mm: int
    rotatable: bool = True


@dataclass
class Placement:
    name: str
    x_mm: int
    y_mm: int
    width_mm: int
    height_mm: int
    rotated: bool = False


def _expanded(rect: Rect) -> list[tuple[int, int, bool]]:
    variants = [(rect.width_mm, rect.height_mm, False)]
    if rect.rotatable and rect.width_mm != rect.height_mm:
        variants.append((rect.height_mm, rect.width_mm, True))
    return variants


def _prune(free: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    kept: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(free):
        x, y, w, h = item
        if w <= 0 or h <= 0:
            continue
        contained = False
        for other_index, other in enumerate(free):
            if index == other_index:
                continue
            ox, oy, ow, oh = other
            if x >= ox and y >= oy and x + w <= ox + ow and y + h <= oy + oh:
                contained = True
                break
        if not contained:
            kept.append(item)
    return kept


def pack_maxrects(
    rects: list[Rect], sheet_w: int, sheet_h: int, kerf: int = SAW_KERF_MM
) -> tuple[list[Placement], list[Rect]]:
    free = [(0, 0, sheet_w, sheet_h)]
    placed: list[Placement] = []
    unplaced: list[Rect] = []
    for rect in sorted(rects, key=lambda r: (r.height_mm, r.width_mm), reverse=True):
        best: tuple[tuple[int, int, int, int], int, int, bool] | None = None
        for w, h, rotated in _expanded(rect):
            for fx, fy, fw, fh in free:
                if w > fw or h > fh:
                    continue
                score = (min(fw - w, fh - h), max(fw - w, fh - h), fy, fx)
                if best is None or score < best[0]:
                    best = (score, fx, fy, rotated)
        if best is None:
            unplaced.append(rect)
            continue
        _, x, y, rotated = best
        w, h = (rect.height_mm, rect.width_mm) if rotated else (rect.width_mm, rect.height_mm)
        placed.append(Placement(rect.name, x, y, w, h, rotated))
        next_free: list[tuple[int, int, int, int]] = []
        cut_w, cut_h = w + kerf, h + kerf
        for fx, fy, fw, fh in free:
            if x >= fx + fw or x + cut_w <= fx or y >= fy + fh or y + cut_h <= fy:
                next_free.append((fx, fy, fw, fh))
                continue
            if x > fx:
                next_free.append((fx, fy, x - fx, fh))
            if x + cut_w < fx + fw:
                next_free.append((x + cut_w, fy, fx + fw - x - cut_w, fh))
            if y > fy:
                next_free.append((fx, fy, fw, y - fy))
            if y + cut_h < fy + fh:
                next_free.append((fx, y + cut_h, fw, fy + fh - y - cut_h))
        free = _prune(next_free)
        if not free:
            break
    return placed, unplaced


def pack_guillotine(
    rects: list[Rect], sheet_w: int, sheet_h: int, kerf: int = SAW_KERF_MM
) -> tuple[list[Placement], list[Rect]]:
    free = [(0, 0, sheet_w, sheet_h)]
    placed: list[Placement] = []
    unplaced: list[Rect] = []
    for rect in sorted(rects, key=lambda r: (r.width_mm * r.height_mm), reverse=True):
        best: tuple[tuple[int, int, int, int], int, int, int, bool] | None = None
        for w, h, rotated in _expanded(rect):
            for index, (fx, fy, fw, fh) in enumerate(free):
                if w > fw or h > fh:
                    continue
                score = (fw * fh - w * h, fy, fx, index)
                if best is None or score < best[0]:
                    best = (score, fx, fy, index, rotated)
        if best is None:
            unplaced.append(rect)
            continue
        _, x, y, index, rotated = best
        w, h = (rect.height_mm, rect.width_mm) if rotated else (rect.width_mm, rect.height_mm)
        placed.append(Placement(rect.name, x, y, w, h, rotated))
        fx, fy, fw, fh = free.pop(index)
        right_w = fw - w - kerf
        bottom_h = fh - h - kerf
        if right_w >= bottom_h:
            if right_w > 0:
                free.append((x + w + kerf, fy, right_w, fh))
            if bottom_h > 0:
                free.append((fx, y + h + kerf, w, bottom_h))
        else:
            if bottom_h > 0:
                free.append((fx, y + h + kerf, fw, bottom_h))
            if right_w > 0:
                free.append((x + w + kerf, fy, right_w, h))
    return placed, unplaced


def pack_skyline(
    rects: list[Rect], sheet_w: int, sheet_h: int, kerf: int = SAW_KERF_MM
) -> tuple[list[Placement], list[Rect]]:
    skyline: list[tuple[int, int, int]] = [(0, sheet_w, 0)]
    placed: list[Placement] = []
    unplaced: list[Rect] = []

    def height_at(x: int, width: int) -> int:
        return max(
            (y for sx, sw, y in skyline if sx < x + width and sx + sw > x),
            default=0,
        )

    for rect in sorted(rects, key=lambda r: (r.height_mm, r.width_mm), reverse=True):
        best: tuple[tuple[int, int, int], int, int, bool] | None = None
        for w, h, rotated in _expanded(rect):
            for sx, sw, _ in skyline:
                if w > sw:
                    continue
                y = height_at(sx, w)
                if y + h > sheet_h:
                    continue
                score = (y + h, -w, sx)
                if best is None or score < best[0]:
                    best = (score, sx, y, rotated)
        if best is None:
            unplaced.append(rect)
            continue
        _, x, y, rotated = best
        w, h = (rect.height_mm, rect.width_mm) if rotated else (rect.width_mm, rect.height_mm)
        placed.append(Placement(rect.name, x, y, w, h, rotated))
        top = y + h
        updated: list[tuple[int, int, int]] = []
        for sx, sw, sy in skyline:
            if sx + sw <= x or sx >= x + w:
                updated.append((sx, sw, sy))
                continue
            if sx < x:
                updated.append((sx, x - sx, sy))
            if sx + sw > x + w:
                updated.append((x + w, sx + sw - x - w, sy))
        updated.append((x, w, top))
        updated.sort()
        merged: list[tuple[int, int, int]] = []
        for sx, sw, sy in updated:
            if merged and merged[-1][2] == sy and merged[-1][0] + merged[-1][1] == sx:
                px, pw, py = merged[-1]
                merged[-1] = (px, pw + sw, py)
            else:
                merged.append((sx, sw, sy))
        skyline = merged
    return placed, unplaced


ALGORITHMS = {
    "maxrects": pack_maxrects,
    "guillotine": pack_guillotine,
    "skyline": pack_skyline,
}


def pack_multi(
    algorithm,
    rects: list[Rect],
    sheet_w: int,
    sheet_h: int,
    kerf: int = SAW_KERF_MM,
) -> tuple[list[list[Placement]], list[Rect]]:
    """Упаковать на несколько листов, пока алгоритм размещает хоть что-то."""
    remaining = list(rects)
    sheets: list[list[Placement]] = []
    while remaining:
        placed, unplaced = algorithm(remaining, sheet_w, sheet_h, kerf)
        if not placed:
            return sheets, unplaced
        sheets.append(placed)
        remaining = unplaced
    return sheets, []


def layouts_to_svg(
    sheets: list[list[Placement]],
    sheet_w: int,
    sheet_h: int,
    path: str,
) -> str:
    """Нарисовать карты листов в SVG для визуальной проверки."""
    from pathlib import Path
    from xml.sax.saxutils import escape

    gap = 20
    width = sheet_w + gap
    height = max(1, len(sheets)) * (sheet_h + gap)
    chunks = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
         f'height="{height}" viewBox="0 0 {width} {height}">'),
        '<rect width="100%" height="100%" fill="#f4f5f7"/>',
    ]
    for index, placements in enumerate(sheets):
        offset_y = index * (sheet_h + gap)
        chunks.append(
            f'<rect x="0" y="{offset_y}" width="{sheet_w}" height="{sheet_h}" '
            'fill="#ffffff" stroke="#1d2330" stroke-width="2"/>'
        )
        for item in placements:
            chunks.append(
                f'<rect x="{item.x_mm}" y="{offset_y + item.y_mm}" '
                f'width="{item.width_mm}" height="{item.height_mm}" '
                'fill="#cfe3ff" stroke="#2b5c9a" stroke-width="1"/>'
            )
            chunks.append(
                f'<text x="{item.x_mm + 4}" y="{offset_y + item.y_mm + 14}" '
                'font-family="Arial" font-size="12" fill="#1d2330">'
                f"{escape(item.name[:24])}</text>"
            )
    chunks.append("</svg>")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(chunks), encoding="utf-8")
    return str(target)
