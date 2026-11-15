"""S5-02: каталог материалов с атрибутами."""

from types import SimpleNamespace

from app.domain.catalog import build_catalog


def _item(name, category, brand="", price=1000, unit="лист", thickness=None):
    return SimpleNamespace(
        name=name, category=category, brand=brand, unit_price=price,
        price_unit=unit, thickness_mm=thickness, subcategory=None,
    )


def test_ldsp_gets_brand_sheet_format():
    catalog = build_catalog([
        _item("LAMARTY однотон", "ЛДСП", brand="LAMARTY", price=5100),
        _item("EGGER ЛДСП", "ЛДСП", brand="EGGER", price=6000),
    ])
    lamarty = catalog.by_kind("ldsp")[0]
    assert (lamarty.sheet_width_mm, lamarty.sheet_height_mm) == (2800, 2070)
    assert lamarty.price == 5100
    egger = catalog.by_kind("ldsp")[1]
    assert (egger.sheet_width_mm, egger.sheet_height_mm) == (2750, 1830)


def test_edge_keeps_thickness_and_no_sheet():
    catalog = build_catalog([
        _item("EGGER 0,8*19", "Кромка", price=34, unit="м", thickness=0.8),
    ])
    edge = catalog.by_kind("edge")[0]
    assert edge.thickness_mm == 0.8
    assert edge.sheet_width_mm is None


def test_hdf_and_counts_and_price_lookup():
    catalog = build_catalog([
        _item("ХДФ", "ХДФ", price=1380),
        _item("Петля Hettich", "Петли", brand="HETTICH", price=357, unit="шт"),
        _item("Неизвестно", "Прочее", price=1),
    ])
    assert catalog.counts() == {"hdf": 1, "hardware": 1}
    assert catalog.price("hdf") == 1380
    assert catalog.price("hardware", "hettich") == 357
