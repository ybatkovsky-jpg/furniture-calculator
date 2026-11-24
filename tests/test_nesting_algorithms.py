"""Бенчмарк алгоритмов раскроя этапа 4."""

from __future__ import annotations

import pytest

from app.domain.nesting_algorithms import (
    ALGORITHMS,
    Rect,
    layouts_to_svg,
    pack_guillotine,
    pack_maxrects,
    pack_multi,
    pack_skyline,
)


def _assert_no_overlap(placements) -> None:
    for index, first in enumerate(placements):
        for second in placements[index + 1:]:
            assert not (
                first.x_mm < second.x_mm + second.width_mm
                and second.x_mm < first.x_mm + first.width_mm
                and first.y_mm < second.y_mm + second.height_mm
                and second.y_mm < first.y_mm + first.height_mm
            ), (first, second)


@pytest.mark.parametrize("name", ["maxrects", "guillotine", "skyline"])
def test_all_small_parts_are_placed(name) -> None:
    parts = [
        Rect("боковина", 600, 800),
        Rect("полка", 500, 300),
        Rect("дно", 600, 300),
        Rect("фасад", 400, 700, rotatable=False),
    ]
    placed, unplaced = ALGORITHMS[name](parts, 2800, 2070)

    assert not unplaced
    assert len(placed) == len(parts)
    assert all(0 <= p.x_mm and 0 <= p.y_mm for p in placed)
    assert all(p.x_mm + p.width_mm <= 2800 for p in placed)
    assert all(p.y_mm + p.height_mm <= 2070 for p in placed)
    _assert_no_overlap(placed)


def test_oversized_part_is_reported() -> None:
    parts = [Rect("слишком", 3000, 100)]
    placed, unplaced = pack_maxrects(parts, 2800, 2070)

    assert not placed
    assert [part.name for part in unplaced] == ["слишком"]


def test_rotation_is_used_for_wide_part() -> None:
    placed, unplaced = pack_skyline([Rect("широкая", 900, 500)], 600, 1000)

    assert not unplaced
    assert placed[0].rotated is True
    assert placed[0].width_mm == 500
    assert placed[0].height_mm == 900


def test_algorithm_names_are_complete() -> None:
    assert set(ALGORITHMS) == {"maxrects", "guillotine", "skyline"}


def test_layout_svg_is_created(tmp_path) -> None:
    placed, _ = pack_maxrects([Rect("боковина", 600, 800)], 2800, 2070)
    target = layouts_to_svg([placed], 2800, 2070, str(tmp_path / "map.svg"))

    content = (tmp_path / "map.svg").read_text(encoding="utf-8")
    assert target.endswith("map.svg")
    assert "<svg" in content
    assert "боковина" in content
    assert content.count("<rect") >= 2


def test_multi_sheet_places_all_small_parts() -> None:
    parts = [Rect(f"p{i}", 1200, 900) for i in range(8)]
    sheets, unplaced = pack_multi(pack_maxrects, parts, 2800, 2070)

    assert not unplaced
    assert len(sheets) >= 2
    assert sum(len(sheet) for sheet in sheets) == len(parts)


def test_guillotine_and_maxrects_are_deterministic() -> None:
    parts = [Rect("a", 400, 300), Rect("b", 300, 200)]
    first = pack_guillotine(parts, 1000, 1000)
    second = pack_guillotine(parts, 1000, 1000)
    assert first == second
    assert pack_maxrects(parts, 1000, 1000) == pack_maxrects(parts, 1000, 1000)
