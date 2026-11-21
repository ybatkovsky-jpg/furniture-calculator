"""Коммерческое предложение в DOCX — правится в Word.

Дизайн: тёмная шапка с логотипом, акцентные разделы, галерея визуализаций
(картинки мебели из проекта), состав таблицей, цены вариантов, условия.
Колонтитул с контактами и номером страницы на каждой странице.
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

logger = logging.getLogger(__name__)

DARK = "1D2330"
ACCENT = "2F6FED"
LIGHT = "F2F5FA"
MUTED = "6B7484"
LINE = "D8DEE9"

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
    """Убрать рамки таблицы."""
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    properties.append(borders)


def _hairline(table, color: str = LINE) -> None:
    """Тонкие горизонтальные линии между строками."""
    properties = table._tbl.tblPr
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


def _no_split(table) -> None:
    """Запретить разрыв строк таблицы между страницами."""
    for row in table.rows:
        properties = row._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        properties.append(cant)


def _cell_text(cell, text: str, *, bold: bool = False, size: int = 10,
               color: str | None = None, align: str = "left") -> None:
    """Записать текст в ячейку с оформлением."""
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(1)
    paragraph.paragraph_format.space_after = Pt(1)
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
               color: str | None = None, space_after: int = 4, space_before: int = 0,
               align: str = "left"):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.space_before = Pt(space_before)
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


def _section_title(document, text: str) -> None:
    """Заголовок раздела с акцентной линией под ним."""
    heading = document.add_paragraph()
    heading.paragraph_format.space_before = Pt(18)
    heading.paragraph_format.space_after = Pt(0)
    run = heading.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor.from_string(DARK)
    run.font.name = "Calibri"
    heading.paragraph_format.keep_with_next = True

    underline = document.add_paragraph()
    underline.paragraph_format.space_before = Pt(0)
    underline.paragraph_format.space_after = Pt(8)
    underline.paragraph_format.keep_with_next = True
    properties = underline._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:color"), ACCENT)
    borders.append(bottom)
    properties.append(borders)


def _header(document, company: dict) -> None:
    """Тёмная шапка: логотип слева, название и реквизиты справа."""
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_borders(table)
    left, right = table.rows[0].cells
    left.width = Cm(4.6)
    right.width = Cm(12.4)
    _shade(left, DARK)
    _shade(right, DARK)

    left.text = ""
    logo_path = company.get("logo_path")
    logo_added = False
    if logo_path:
        try:
            paragraph = left.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(4)
            paragraph.add_run().add_picture(str(logo_path), width=Cm(2.8))
            logo_added = True
        except Exception:
            logger.warning("Логотип не вставлен: %s", logo_path, exc_info=True)

    right.text = ""
    first = right.paragraphs[0]
    first.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    first.paragraph_format.space_before = Pt(6)
    if not logo_added:
        # Логотип не вставился — название выводим текстом.
        run = first.add_run(company.get("name", "ПРОМЕБЕЛЬ"))
        run.bold = True
        run.font.size = Pt(16)
        run.font.color.rgb = RGBColor.from_string("FFFFFF")

    tagline = right.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    tagline.paragraph_format.space_after = Pt(4)
    run = tagline.add_run(company.get("tagline") or TAGLINE)
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string("9AA4B5")

    for line in company.get("requisites") or []:
        paragraph = right.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run(line)
        run.font.size = Pt(7.5)
        run.font.color.rgb = RGBColor.from_string("C9D1DE")

    spacer = right.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)

    if not logo_added:
        logger.info("Логотип не задан — шапка только с названием")


def _footer(document, company: dict) -> None:
    """Колонтитул: контакты слева, номер страницы справа."""
    section = document.sections[0]
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    contacts = "  ·  ".join(
        part for part in (company.get("phone"), company.get("email")) if part
    )
    run = paragraph.add_run(contacts)
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(MUTED)
    run = paragraph.add_run("\t")
    run.font.size = Pt(8)
    page_run = paragraph.add_run("стр. ")
    page_run.font.size = Pt(8)
    page_run.font.color.rgb = RGBColor.from_string(MUTED)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)


def _normalize_image(path: str) -> str:
    """Привести картинку к пропорции 4:3 (обрезка по центру) для ровной сетки.

    Возвращает путь к временному файлу; при любой ошибке — исходный путь.
    """
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            target_ratio = 4 / 3
            if width / height > target_ratio:
                new_width = int(height * target_ratio)
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
            else:
                new_height = int(width / target_ratio)
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))
            if image.width > 1600:
                image = image.resize((1600, int(1600 / target_ratio)))

            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
                image.save(handle.name, "JPEG", quality=88)
                return handle.name
    except Exception:  # без обработки вставим как есть
        logger.warning("Картинка не нормализована: %s", path, exc_info=True)
        return path


def _gallery(document, images: list[str], width_cm: float = 8.2) -> None:
    """Галерея визуализаций: по две картинки в ряд, пропорции выровнены."""
    valid = []
    for path in images[:6]:
        try:
            from PIL import Image

            with Image.open(path) as image:
                if image.width < 40 or image.height < 40:
                    continue
            valid.append(_normalize_image(path))
        except Exception:
            logger.warning("Картинка пропущена: %s", path, exc_info=True)

    if not valid:
        return

    _section_title(document, "Визуализация проекта")
    for start in range(0, len(valid), 2):
        pair = valid[start:start + 2]
        table = document.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        _no_borders(table)
        for index, path in enumerate(pair):
            cell = table.rows[0].cells[index]
            cell.width = Cm(width_cm)
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(10)
            try:
                paragraph.add_run().add_picture(path, width=Cm(width_cm - 0.6))
            except Exception:
                logger.warning("Картинка не вставлена: %s", path, exc_info=True)
        if len(pair) == 1:
            table.rows[0].cells[1].width = Cm(width_cm)


def _variant_block(document, variant: Any, number: int) -> None:
    """Блок варианта: название, описание, материалы, состав, цена."""
    heading = document.add_paragraph()
    heading.paragraph_format.space_before = Pt(16)
    heading.paragraph_format.space_after = Pt(1)
    run = heading.add_run(f"Вариант {number}. {variant.name}")
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = RGBColor.from_string(DARK)

    marker = document.add_paragraph()
    marker.paragraph_format.space_after = Pt(6)
    run = marker.add_run(variant.description)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(MUTED)
    run.italic = True

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

    gap = document.add_paragraph()
    gap.paragraph_format.space_after = Pt(2)
    gap.paragraph_format.space_before = Pt(0)

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
    _no_split(table)
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
            _cell_text(cell, value, size=9, align="left" if index == 0 else "center")

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
    images: list[str] | None = None,
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
    section.top_margin = Cm(1.2)
    section.bottom_margin = Cm(1.6)

    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")

    _header(document, company)
    _footer(document, company)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(10)
    title.paragraph_format.space_after = Pt(2)
    run = title.add_run("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor.from_string(DARK)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(2)
    run = subtitle.add_run(f"по изготовлению корпусной мебели от {kp_date:%d.%m.%Y}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    validity = document.add_paragraph()
    validity.alignment = WD_ALIGN_PARAGRAPH.CENTER
    validity.paragraph_format.space_after = Pt(6)
    run = validity.add_run(f"Предложение действительно до {valid_until:%d.%m.%Y}")
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    if client:
        _paragraph(document, f"Заказчик: {client}", size=11, bold=True,
                   space_after=4, space_before=8)

    gallery = list(images or [])
    if image_path:
        gallery.append(image_path)
    _gallery(document, gallery)
    if gallery:
        # Картинки проекта занимают свою страницу, варианты начинаются с новой —
        # иначе сводная таблица рвётся между страницами.
        document.add_page_break()

    _section_title(document, "Варианты и стоимость")
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
    _no_split(summary)
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

    _section_title(document, "Состав и комплектация")
    for number, variant in enumerate(variants, start=1):
        if number > 1:
            # Каждый вариант — со своей страницы: таблица состава не рвётся.
            document.add_page_break()
        _variant_block(document, variant, number)

    if options_text:
        _section_title(document, "Дополнительно включено")
        for line in options_text:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(f"• {line}")
            run.font.size = Pt(9)

    if conditions:
        _section_title(document, "Условия")
        for line in conditions:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(f"• {line}")
            run.font.size = Pt(9)

    signature = document.add_paragraph()
    signature.paragraph_format.space_before = Pt(22)
    run = signature.add_run("С уважением, команда ПРОМЕБЕЛЬ")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
