"""Тесты импорта прайса: P0-01 (цена/категория/единица), P0-07, P1-16."""

import asyncio
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, PriceHistory, PriceItem
from app.services.price_manager import (
    detect_category_from_text,
    detect_thickness_mm,
    import_price_from_xlsx,
    parse_number,
)


# ── Чистые функции ──────────────────────────────────────────────────────────


def test_parse_number_does_not_extract_digits_from_name():
    """«LAMARTY 0,4*19» — не цена: раньше давало 0,419 ₽."""
    assert parse_number("LAMARTY 0,4*19") is None
    assert parse_number("ЛДСП EGGER 2800*2070") is None


def test_parse_number_accepts_real_price_cells():
    assert parse_number("1 250,50") == 1250.5
    assert parse_number("1250.5 ₽") == 1250.5
    assert parse_number("1250 руб.") == 1250.0
    assert parse_number(1250) == 1250.0
    assert parse_number(None) is None
    assert parse_number("") is None


def test_specific_edge_marker_wins_over_ldsp():
    assert detect_category_from_text("КРОМКА К ЛДСП") == "Кромка"
    assert detect_category_from_text("Кромка ПВХ 0.4") == "Кромка"
    assert detect_category_from_text("ЛДСП EGGER H1334") == "ЛДСП"
    assert detect_category_from_text("Петля BLUM") == "Петли"


def test_thickness_is_parsed_explicitly_not_by_substring():
    assert detect_thickness_mm("Кромка 0.8*22", "Кромка") == 0.8
    assert detect_thickness_mm("Кромка 0,8*19", "Кромка") == 0.8
    assert detect_thickness_mm("Кромка 2*19", "Кромка") == 2.0
    assert detect_thickness_mm("Кромка ПВХ 0.4", "Кромка") == 0.4
    assert detect_thickness_mm("Кромка HPL 2 мм", "Кромка") == 2.0
    # У ЛДСП «2800*2070» — формат листа, толщиной не считается.
    assert detect_thickness_mm("ЛДСП EGGER 2800*2070", "ЛДСП") is None


# ── Импорт XLSX ─────────────────────────────────────────────────────────────


def _make_xlsx(path: Path, rows: list[tuple[str, object]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Наименование", "Цена"])
    for name, price in rows:
        ws.append([name, price])
    wb.save(path)


def _run_import(
    imports: list[list[tuple[str, object]]],
    tmp_dir: Path,
    modes: list[dict] | None = None,
):
    """Импортировать файлы по очереди (каждый элемент — свой прайс) и вернуть снимок.

    `modes` — параметры импорта для каждого файла по порядку (например,
    `update_only=True` для второго файла).
    """

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        stats = []
        async with factory() as session:
            for index, price_rows in enumerate(imports):
                # P2-17: файл — во временном каталоге теста, не в repo/temp.
                path = tmp_dir / f"test_price_{index}.xlsx"
                _make_xlsx(path, price_rows)
                kwargs = modes[index] if modes else {}
                stats.append(
                    await import_price_from_xlsx(
                        str(path), session, changed_by="test", **kwargs
                    )
                )
            items = (
                await session.execute(select(PriceItem).order_by(PriceItem.id))
            ).scalars().all()
            history = (
                await session.execute(select(PriceHistory))
            ).scalars().all()
            snapshot = [
                {
                    "name": i.name,
                    "category": i.category,
                    "unit": i.price_unit,
                    "price": i.unit_price,
                    "manual": i.requires_manual_price,
                    "thickness": i.thickness_mm,
                    "active": i.is_active,
                }
                for i in items
            ]
        await engine.dispose()
        return stats, snapshot, history

    return asyncio.run(scenario())


def test_import_empty_price_requires_manual_price(tmp_path):
    """P0-01: пустая цена → NULL + requires_manual_price, а не 0,419 из имени."""
    stats, items, _ = _run_import([[("ЛДСП LAMARTY 2800*2070", None)]], tmp_path)

    assert stats[0]["created"] == 1
    item = items[0]
    assert item["price"] is None
    assert item["manual"] == 1
    assert item["category"] == "ЛДСП"
    assert item["unit"] == "лист"
    assert item["thickness"] is None


def test_import_edge_gets_unit_thickness_and_category(tmp_path):
    stats, items, _ = _run_import([[("Кромка к ЛДСП 0.8*22", 45)]], tmp_path)

    item = items[0]
    assert item["category"] == "Кромка"
    assert item["unit"] == "м"
    assert item["thickness"] == 0.8
    assert item["price"] == 45
    assert item["manual"] == 0


def test_import_edge_without_thickness_requires_manual(tmp_path):
    """P0-07: толщина не угадывается — позиция ждёт ручного подтверждения."""
    _, items, _ = _run_import([[("Кромка EGGER", 45)]], tmp_path)
    assert items[0]["manual"] == 1
    assert items[0]["price"] is None


def test_import_rejects_negative_price(tmp_path):
    stats, items, _ = _run_import([[("ЛДСП EGGER", -10)]], tmp_path)
    assert stats[0]["rejected"] == 1
    assert items == []


def test_import_writes_price_history_once(tmp_path):
    """P1-16: история пишется по факту изменения, а не после присваивания."""
    changes = [
        [("ЛДСП EGGER", 5600)],
        [("ЛДСП EGGER", 5900)],
        [("ЛДСП EGGER", 5900)],
    ]
    stats, items, history = _run_import(changes, tmp_path)

    assert stats[0]["created"] == 1
    assert stats[1]["updated"] == 1
    assert stats[1]["skipped"] == 0
    assert stats[2]["updated"] == 0
    assert stats[2]["skipped"] == 1
    assert len(history) == 1
    assert history[0].old_price == 5600
    assert history[0].new_price == 5900
    assert items[0]["price"] == 5900


def test_import_deactivates_items_missing_from_new_file(tmp_path):
    """Внутри загруженной категории пропавшие позиции деактивируются."""
    changes = [
        [("ЛДСП EGGER", 5600), ("ЛДСП LAMARTY", 7000)],
        [("ЛДСП EGGER", 5600)],
    ]
    stats, items, _ = _run_import(changes, tmp_path)

    assert stats[1]["deactivated"] == 1
    by_name = {i["name"]: i for i in items}
    assert by_name["ЛДСП EGGER"]["active"] == 1
    assert by_name["ЛДСП LAMARTY"]["active"] == 0


def test_partial_price_file_does_not_disable_other_categories(tmp_path):
    """Загрузка только ЛДСП не должна гасить кромку/фурнитуру."""
    changes = [
        [("ЛДСП EGGER", 5600), ("Кромка 0.8*22", 45)],
        [("ЛДСП EGGER", 5600)],
    ]
    stats, items, _ = _run_import(changes, tmp_path)

    assert stats[1]["deactivated"] == 0
    by_name = {i["name"]: i for i in items}
    assert by_name["Кромка 0.8*22"]["active"] == 1


def test_duplicate_rows_last_occurrence_wins(tmp_path):
    """В прайсе есть дубли одной позиции: итог — последняя строка файла.

    Итоговая цена не зависит от порядка промежуточных значений; известное
    следствие — «мерцающие» записи в истории цен (см.
    docs/price_baseline_2026-11-17.md), итоговые данные при этом верны.
    """
    stats, items, _ = _run_import(
        [[("ЛДСП EGGER", 5600), ("ЛДСП EGGER", 6000), ("ЛДСП EGGER", 5600)]],
        tmp_path,
    )

    assert stats[0]["created"] == 1
    assert len(items) == 1
    assert items[0]["price"] == 5600
    assert items[0]["active"] == 1


def test_update_only_does_not_create_or_deactivate(tmp_path):
    """Файл-расчёт не должен плодить дубли и гасить отсутствующие позиции."""
    changes = [
        [("ЛДСП EGGER", 5600), ("ЛДСП LAMARTY", 7000)],
        [("ЛДСП EGGER", 5900)],
    ]
    stats, items, _ = _run_import(
        changes, tmp_path, modes=[{}, {"update_only": True}]
    )

    assert stats[1]["created"] == 0
    assert stats[1]["deactivated"] == 0
    by_name = {i["name"]: i for i in items}
    assert by_name["ЛДСП EGGER"]["price"] == 5900
    assert by_name["ЛДСП LAMARTY"]["price"] == 7000
    assert by_name["ЛДСП LAMARTY"]["active"] == 1


def test_update_only_keeps_price_when_cell_empty(tmp_path):
    """Пустая ячейка цены в рабочем файле не стирает известную цену."""
    changes = [
        [("ЛДСП EGGER", 5600)],
        [("ЛДСП EGGER", None)],
    ]
    stats, items, _ = _run_import(
        changes, tmp_path, modes=[{}, {"update_only": True}]
    )

    assert stats[1]["updated"] == 0
    assert items[0]["price"] == 5600
    assert items[0]["manual"] == 0
