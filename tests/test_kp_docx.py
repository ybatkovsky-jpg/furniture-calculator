"""DOCX коммерческого предложения: структура и содержимое."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from docx import Document

from app.services.kp_docx import build_kp_docx
from app.services.kp_variants import build_variants
from tests.test_kp_variants import catalog

MODULES = [
    {
        "type": "lower_base",
        "width": 800,
        "depth": 560,
        "height": 820,
        "quantity": 2,
        "facades": {"count": 2},
        "drawers": {"count": 1},
    }
]

COMPANY = {
    "name": "ПРОМЕБЕЛЬ",
    "phone": "+7 999 000 00 00",
    "email": "test@example.com",
    "requisites": ["ООО «Тест»", "ИНН 0000000000"],
}


def _build():
    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    return build_kp_docx(
        variants=variants,
        company=COMPANY,
        kp_date=date(2026, 11, 20),
        valid_until=date(2026, 12, 4),
        client="Иванов И. И.",
        options_text=["Светодиодная подсветка"],
        conditions=["Срок изготовления — 30 рабочих дней"],
    )


def test_document_has_title_and_client() -> None:
    document = Document(BytesIO(_build()))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ" in text
    assert "Иванов И. И." in text
    assert "04.12.2026" in text


def test_document_has_three_variant_blocks() -> None:
    document = Document(BytesIO(_build()))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for name in ("Вариант 1. Базовая", "Вариант 2. Стандарт", "Вариант 3. Комфорт"):
        assert name in text


def test_summary_table_has_all_variants() -> None:
    document = Document(BytesIO(_build()))
    summary = document.tables[1]
    rows = [[cell.text.strip() for cell in row.cells] for row in summary.rows]
    assert rows[0][0] == "Вариант"
    names = [row[0] for row in rows[1:]]
    assert names == ["Базовая", "Стандарт", "Комфорт"]
    for row in rows[1:]:
        assert row[2].endswith("₽") and row[3].endswith("₽")


def test_module_table_lists_composition() -> None:
    document = Document(BytesIO(_build()))
    composition = document.tables[3]  # 0 шапка, 1 сводка, 2 материалы
    header = [cell.text.strip() for cell in composition.rows[0].cells]
    assert header == ["Модуль", "Размеры, мм", "Корпусов", "Двери", "Ящики"]
    assert len(composition.rows) == 2  # шапка + один модуль


def test_document_is_editable_docx_not_image() -> None:
    """КП должно правиться: текст ищется в документе, а не в картинке."""
    payload = _build()
    assert payload[:2] == b"PK"
    document = Document(BytesIO(payload))
    assert len(document.paragraphs) > 5
    assert document.tables


def test_logo_is_inserted_in_header(tmp_path) -> None:
    """Логотип — картинка в шапке, а не текст-заглушка."""
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 200), (29, 35, 48)).save(logo)

    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    payload = build_kp_docx(
        variants=variants,
        company={**COMPANY, "logo_path": logo},
        kp_date=date(2026, 11, 20),
    )
    document = Document(BytesIO(payload))
    assert len(document.inline_shapes) == 1
    assert round(document.inline_shapes[0].width.cm, 1) == 3.4


def test_without_logo_brand_is_text() -> None:
    """Нет файла логотипа — КП всё равно собирается, бренд текстом."""
    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    payload = build_kp_docx(variants=variants, company=COMPANY, kp_date=date(2026, 11, 20))
    document = Document(BytesIO(payload))
    assert len(document.inline_shapes) == 0
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ" in text
