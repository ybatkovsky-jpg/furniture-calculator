"""Фирменный PDF коммерческого предложения (этап 9).

PDF собирается из того же снимка/вариантов, что DOCX: только клиентские
цены, без себестоимости, маржи и закупочных цен.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.kp_variants import Variant

logger = logging.getLogger(__name__)

FONT_CANDIDATES = (
    ("KPRegular", Path("C:/Windows/Fonts/arial.ttf")),
    ("KPRegular", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
)
BOLD_CANDIDATES = (
    ("KPBold", Path("C:/Windows/Fonts/arialbd.ttf")),
    ("KPBold", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
)


def _register_fonts() -> tuple[str, str]:
    regular = "Helvetica"
    bold = "Helvetica-Bold"
    for name, path in FONT_CANDIDATES:
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            regular = name
            break
    for name, path in BOLD_CANDIDATES:
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            bold = name
            break
    return regular, bold


def _money(value: float) -> str:
    return f"{round(value):,}".replace(",", " ") + " ₽"


def build_kp_pdf(
    path: str | Path,
    *,
    client: str,
    variants: list[Variant],
    company: dict,
    validity_days: int = 14,
    options_text: list[str] | None = None,
    images: list[str] | None = None,
) -> str:
    """Собрать PDF; возвращает путь к файлу."""
    regular, bold = _register_fonts()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "KPTitle", parent=styles["Title"], fontName=bold, fontSize=20,
        leading=24, textColor=colors.HexColor("#1d2330"),
    )
    subtitle = ParagraphStyle(
        "KPSubtitle", parent=styles["Normal"], fontName=regular, fontSize=10,
        leading=14, textColor=colors.HexColor("#5b6576"),
    )
    body = ParagraphStyle(
        "KPBody", parent=styles["Normal"], fontName=regular, fontSize=9.5,
        leading=13, textColor=colors.HexColor("#1d2330"),
    )
    small = ParagraphStyle(
        "KPSmall", parent=body, fontSize=8, leading=11,
        textColor=colors.HexColor("#5b6576"),
    )

    document = SimpleDocTemplate(
        str(target), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Коммерческое предложение",
        author=str(company.get("name") or "ПРОМЕБЕЛЬ"),
    )
    story = [
        Paragraph(escape(str(company.get("name") or "ПРОМЕБЕЛЬ")), title),
        Paragraph(escape(str(company.get("tagline") or "")), subtitle),
        Spacer(1, 5 * mm),
        Paragraph(
            f"Коммерческое предложение от {date.today():%d.%m.%Y} "
            f"для {escape(client or 'клиента')}",
            body,
        ),
        Paragraph(
            f"Действует {int(validity_days)} дней. Цены в рублях, "
            "округление до рубля.",
            small,
        ),
        Spacer(1, 6 * mm),
        Paragraph("Варианты комплектации", ParagraphStyle(
            "KPSection", parent=body, fontName=bold, fontSize=13, leading=16,
        )),
        Spacer(1, 3 * mm),
    ]

    rows = [["Вариант", "Что входит", "Наличные", "Безнал"]]
    for variant in variants:
        rows.append([
            Paragraph(escape(variant.name), body),
            Paragraph(escape(variant.materials_text or "—"), small),
            Paragraph(_money(variant.final_cash), body),
            Paragraph(_money(variant.final_noncash), body),
        ])
    table = Table(rows, colWidths=[30 * mm, 85 * mm, 27 * mm, 27 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d2330")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d6dce6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f5f7")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    if options_text:
        story += [Spacer(1, 5 * mm), Paragraph("Дополнительно", body)]
        for option in options_text:
            story.append(Paragraph(f"• {escape(str(option))}", small))

    for image_path in (images or [])[:6]:
        source = Path(image_path)
        if not source.exists():
            continue
        try:
            story += [Spacer(1, 4 * mm), Image(str(source), width=165 * mm, height=110 * mm,
                                               kind="proportional")]
        except Exception:  # noqa: BLE001 — битая картинка не должна ломать КП
            logger.warning("Картинка КП пропущена: %s", source)
            continue

    story += [
        Spacer(1, 8 * mm),
        Paragraph(
            "Коммерческое предложение действительно при указанных сроках. "
            "Итоговая стоимость фиксируется в договоре.",
            small,
        ),
    ]

    document.build(story)
    return str(target)
