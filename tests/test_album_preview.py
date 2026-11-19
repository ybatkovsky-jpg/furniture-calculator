"""День 3: предпросмотр распознавания и альбома (комнаты не смешиваются)."""

from types import SimpleNamespace

from app.bot.handlers.image import (
    _count,
    _module_to_dict,
    build_album_preview,
    format_recognition_result,
)
from app.services.image_analyzer import RecognitionResult


def _module(**kwargs):
    base = {
        "type": "lower_base",
        "width": 600,
        "depth": 560,
        "height": 820,
        "quantity": 1,
        "has_glass": False,
        "facades": None,
        "drawers": None,
        "shelves": 0,
        "is_corner": False,
        "notes": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_count_handles_dict_int_and_none():
    assert _count({"count": 3}) == 3
    assert _count({"count": "2"}) == 2
    assert _count(1) == 1
    assert _count(None) == 0
    assert _count({"count": None}) == 0


def test_recognition_result_shows_height_doors_drawers_glass():
    result = RecognitionResult(
        modules=[
            _module(
                quantity=2,
                facades={"count": 1, "type": "doors"},
                drawers={"count": 1},
            ),
            _module(
                type="upper_base",
                width=600,
                depth=320,
                height=720,
                has_glass=True,
                facades={"count": 1, "type": "doors"},
            ),
        ],
        confidence="medium",
        zone_type="Кухня",
    )

    text = format_recognition_result(result)

    assert "600×560×820" in text
    assert "2 корп." in text
    assert "2 двер." in text
    assert "2 ящ." in text
    assert "600×320×720" in text
    assert "стекло" in text
    assert "Зона: Кухня" in text
    assert "Уверенность распознавания: medium" in text


def test_module_to_dict_keeps_doors_drawers_and_corner():
    module = _module(
        type="corner",
        is_corner=True,
        facades={"count": 2},
        drawers={"count": 1},
        shelves=1,
        notes="ширина исправлена",
    )
    data = _module_to_dict(module)
    assert data["type"] == "corner"
    assert data["is_corner"] is True
    assert data["facades"] == {"count": 2}
    assert data["drawers"] == {"count": 1}
    assert data["shelves"] == 1
    assert data["notes"] == "ширина исправлена"


def test_album_preview_lists_rooms_separately():
    rooms = [
        {
            "name": "Кухня",
            "zone_type": "kitchen",
            "modules": [
                {
                    "type": "lower_base",
                    "width": 600,
                    "depth": 560,
                    "height": 820,
                    "quantity": 2,
                    "facades": {"count": 1},
                    "drawers": {"count": 1},
                    "has_glass": False,
                }
            ],
        },
        {
            "name": "Спальня",
            "zone_type": "bedroom",
            "modules": [
                {
                    "type": "wardrobe",
                    "width": 1200,
                    "depth": 600,
                    "height": 2400,
                    "quantity": 1,
                    "facades": {"count": 3},
                    "drawers": None,
                    "has_glass": False,
                }
            ],
        },
    ]

    text = build_album_preview(rooms)

    assert "Распознано помещений: 2" in text
    assert "🏠 Кухня" in text
    assert "🏠 Спальня" in text
    assert "600×560×820" in text
    assert "2 корп." in text
    assert "2 двер." in text
    assert "1200×600×2400" in text
    assert "3 двер." in text
