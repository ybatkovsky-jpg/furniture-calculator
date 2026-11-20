"""Коммерческое предложение в DOCX — правится в Word.

Дизайн ближе к тому, что уходит клиентам (шапка с реквизитами, заголовок,
варианты с ценами), но собран как редактируемый документ: тексты, цены и
состав — обычные абзацы и таблицы, а не картинка.

`build_kp_docx` принимает подготовленные данные — расчёт уже сделан
(`kp_variants.build_variants`), здесь только вёрстка.
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DARK = "1D2330"
ACCENT = "2F6FED"
LIGHT = "F2F5FA"
MUTED = "6B7484"

logger = logging.getLogger(__name__)

TAGLINE = "качество и индивидуальность"


def _shade(cell, color: str) -> None:
    """Заливка ячейки."""
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    properties.append(shading)


def _no_borders(table) -> None:
    """Убрать рамки таблицы (для шапки и блоков без линий)."""
    tbl = table._tbl
    properties = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    properties.append(borders)


def _hairline(table, color: str = "D8DEE9") -> None:
    """Тонкие горизонтальные линии между строками."""
    tbl = table._tbl
    properties = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    inside = OxmlElement("w:insideH")
    inside.set(qn("w:val"), "single")
    inside.set(qn("w:sz"), "4")
    inside.set(qn("w:color"), color)
    borders.append(inside)
    properties.append(borders)


def _cell_text(cell, text: str, *, bold: bool = False, size: int = 10,
               color: str | None = None, align: str = "left") -> None:
    """Записать текст в ячейку с нужным оформлением."""
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
    }[align]
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _money(value: float) -> str:
    """Цена в рублях с пробелами: 148 000 ₽."""
    return f"{round(value):,}".replace(",", " ") + " ₽"


def _paragraph(document, text: str = "", *, size: int = 10, bold: bool = False,
               color: str | None = None, space_after: int = 4,
               align: str = "left"):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
    }[align]
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    return paragraph


def _header(document, company: dict) -> None:
    """Шапка: название и слоган слева, реквизиты справа."""
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_borders(table)
    left, right = table.rows[0].cells
    left.width = Cm(9)
    right.width = Cm(8)

    left.text = ""
    brand = left.paragraphs[0]
    run = brand.add_run(company.get("name", "ПРО МЕБЕЛЬ"))
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor.from_string(DARK)
    tagline = left.add_paragraph()
    tagline_run = tagline.add_run(company.get("tagline") or TAGLINE)
    tagline_run.font.size = Pt(9)
    tagline_run.font.color.rgb = RGBColor.from_string(MUTED)

    requisites = company.get("requisites") or []
    right.text = ""
    first = right.paragraphs[0]
    first.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    name_run = first.add_run(requisites[0] if requisites else "")
    name_run.font.size = Pt(8)
    name_run.font.color.rgb = RGBColor.from_string(MUTED)
    for line in requisites[1:]:
        paragraph = right.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run(line)
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string(MUTED)


def _variant_block(document, variant: Any, number: int) -> None:
    """Блок одного варианта: название, описание, материалы, состав, цена."""
    heading = document.add_paragraph()
    heading.paragraph_format.space_before = Pt(14)
    heading.paragraph_format.space_after = Pt(2)
    run = heading.add_run(f"Вариант {number}. {variant.name}")
    run.bold = True
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor.from_string(DARK)

    _paragraph(document, variant.description, size=9, color=MUTED, space_after=8)

    materials = document.add_table(rows=1, cols=1)
    _no_borders(materials)
    cell = materials.rows[0].cells[0]
    _shade(cell, LIGHT)
    cell.width = Cm(17)
    cell.text = ""
    for line in variant.materials_text.split("; "):
        paragraph = cell.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(1)
        bullet = paragraph.add_run(f"• {line}")
        bullet.font.size = Pt(9)

    table = document.add_table(rows=1, cols=5)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _hairline(table)
    headers = ("Модуль", "Размеры, мм", "Корпусов", "Двери", "Ящики")
    widths = (Cm(7.4), Cm(4.2), Cm(2.2), Cm(1.8), Cm(1.8))
    for index, title in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.width = widths[index]
        _shade(cell, DARK)
        _cell_text(cell, title, bold=True, size=8,
                   color="FFFFFF", align="left" if index == 0 else "center")

    labels = {
        "lower_base": "Нижняя база",
        "upper_base": "Верхний модуль",
        "penal": "Пенал",
        "wardrobe": "Шкаф",
        "table": "Стол",
        "counter": "Стойка",
        "open_unit": "Открытая тумба",
        "shelf_unit": "Стеллаж",
        "column": "Колонна",
        "tumbler": "Тумба",
        "corner": "Угловой модуль",
    }
    for module in variant.modules or []:
        row = table.add_row()
        facades = module.get("facades") or {}
        drawers = module.get("drawers") or {}
        values = (
            labels.get(str(module.get("type")), str(module.get("type") or "модуль")),
            f"{module.get('width', 0)}×{module.get('depth', 0)}×{module.get('height', 0)}",
            str(module.get("quantity", 1)),
            str(facades.get("count", 0) if isinstance(facades, dict) else 0),
            str(drawers.get("count", 0) if isinstance(drawers, dict) else 0),
        )
        for index, value in enumerate(values):
            cell = row.cells[index]
            cell.width = widths[index]
            _cell_text(cell, value, size=9,
                       align="left" if index == 0 else "center")

    totals = document.add_table(rows=2, cols=2)
    totals.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_borders(totals)
    for row_index, (label, value, bold) in enumerate((
        ("Стоимость при наличной оплате", _money(variant.final_cash), True),
        ("Стоимость при оплате безналом", _money(variant.final_noncash), False),
    )):
        cells = totals.rows[row_index].cells
        cells[0].width = Cm(11.5)
        cells[1].width = Cm(5.5)
        _shade(cells[0], LIGHT if row_index == 0 else "FFFFFF")
        _shade(cells[1], LIGHT if row_index == 0 else "FFFFFF")
        _cell_text(cells[0], label, bold=bold, size=11 if bold else 10)
        _cell_text(cells[1], value, bold=bold, size=13 if bold else 10,
                   color=ACCENT if bold else DARK, align="right")


def build_kp_docx(
    *,
    variants: list[Any],
    company: dict | None = None,
    kp_date: date | None = None,
    valid_until: date | None = None,
    client: str = "",
    options_text: list[str] | None = None,
    conditions: list[str] | None = None,
    image_path: str | None = None,
) -> bytes:
    """Собрать DOCX коммерческого предложения и вернуть байты."""
    company = company or {}
    kp_date = kp_date or date.today()
    valid_until = valid_until or (kp_date + timedelta(days=14))

    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)

    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")

    _header(document, company)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(22)
    title.paragraph_format.space_after = Pt(2)
    run = title.add_run("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ")
    run.bold = True
    run.font.size = Pt(19)
    run.font.color.rgb = RGBColor.from_string(DARK)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(2)
    run = subtitle.add_run(f"по изготовлению корпусной мебели от {kp_date:%d.%m.%Y}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    validity = document.add_paragraph()
    validity.alignment = WD_ALIGN_PARAGRAPH.CENTER
    validity.paragraph_format.space_after = Pt(14)
    run = validity.add_run(f"Предложение действительно до {valid_until:%d.%m.%Y}")
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    if client:
        _paragraph(document, f"Заказчик: {client}", size=11, bold=True, space_after=10)

    # Сводка: три варианта в одну таблицу — клиенту видно разницу сразу.
    _paragraph(document, "Варианты исполнения", size=12, bold=True, space_after=6)
    summary = document.add_table(rows=1, cols=4)
    summary.alignment = WD_TABLE_ALIGNMENT.CENTER
    _hairline(summary)
    headers = ("Вариант", "Что входит", "Наличные", "Безнал")
    widths = (Cm(3.6), Cm(7.6), Cm(2.9), Cm(2.9))
    for index, header in enumerate(headers):
        cell = summary.rows[0].cells[index]
        cell.width = widths[index]
        _shade(cell, DARK)
        _cell_text(cell, header, bold=True, size=8, color="FFFFFF",
                   align="right" if index >= 2 else "left")
    for variant in variants:
        row = summary.add_row()
        values = (variant.name, variant.description,
                  _money(variant.final_cash), _money(variant.final_noncash))
        for index, value in enumerate(values):
            cell = row.cells[index]
            cell.width = widths[index]
            _cell_text(cell, value, size=9, bold=index in (0, 2),
                       color=ACCENT if index == 2 else None,
                       align="right" if index >= 2 else "left")

    for number, variant in enumerate(variants, start=1):
        _variant_block(document, variant, number)

    if options_text:
        _paragraph(document, "Дополнительно включено", size=12, bold=True,
                   space_after=4)
        for line in options_text:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(f"• {line}")
            run.font.size = Pt(9)

    if conditions:
        _paragraph(document, "Условия", size=12, bold=True, space_after=4)
        for line in conditions:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(f"• {line}")
            run.font.size = Pt(9)

    if image_path:
        try:
            _paragraph(document, "Эскиз", size=12, bold=True, space_after=4)
            document.add_picture(image_path, width=Cm(15))
        except Exception:
            logger.warning("Эскиз не вставлен в КП", exc_info=True)

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.space_before = Pt(16)
    run = footer.add_run(
        " ".join(
            part for part in (
                company.get("phone"),
                company.get("email"),
                company.get("address"),
            ) if part
        )
    )
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
