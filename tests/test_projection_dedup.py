"""Модель B: одна мебель в разных проекциях не считается дважды."""

from __future__ import annotations

from app.services.full_pipeline import RoomSpec
from app.services.image_analyzer import RecognizedModule
from app.services.projections import mark_duplicate_projection_rooms


def _module(width: int = 600) -> RecognizedModule:
    return RecognizedModule(
        type="lower_base",
        width=width,
        depth=560,
        height=820,
        quantity=1,
        facades={"count": 1},
    )


def _room(page: int, view: str, modules: list) -> RoomSpec:
    return RoomSpec(
        room_name="Кухня",
        page=page,
        room_id="kitchen",
        view_type=view,
        view_group="kitchen_main",
        modules=modules,
    )


def test_elevation_is_main_and_plan_is_duplicate() -> None:
    main = _room(1, "elevation", [_module()])
    duplicate = _room(2, "plan", [_module()])
    other = _room(3, "section", [_module(400)])

    mark_duplicate_projection_rooms([main, duplicate, other])

    assert main.is_duplicate_view is False
    assert duplicate.is_duplicate_view is True
    assert duplicate.duplicate_of_page == 1
    assert other.is_duplicate_view is False


def test_different_modules_are_not_duplicates() -> None:
    first = _room(1, "elevation", [_module(600)])
    second = _room(2, "plan", [_module(900)])

    mark_duplicate_projection_rooms([first, second])

    assert second.is_duplicate_view is False


def test_plan_and_elevation_width_mismatch_is_warned() -> None:
    elevation = _room(1, "elevation", [_module(600)])
    plan = _room(2, "plan", [_module(900)])

    mark_duplicate_projection_rooms([elevation, plan])

    assert any("Ширина плана" in item for item in elevation.projection_warnings)
    assert any("Ширина плана" in item for item in plan.projection_warnings)


def test_without_view_group_nothing_is_marked() -> None:
    first = _room(1, "elevation", [_module()])
    second = _room(2, "plan", [_module()])
    second.view_group = None

    mark_duplicate_projection_rooms([first, second])

    assert second.is_duplicate_view is False
