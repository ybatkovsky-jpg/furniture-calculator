"""
Тесты гейта no-door: страница с мебелью БЕЗ дверец (кровать/стол/диван)
не должна уходить в legacy-fallback analyze_drawing.

is_no_door_zone_page(zone_type, modules):
    True  → modules пусто И зона из NO_DOOR_ZONES → fallback не нужен,
            страница помечается «не корпусная мебель».
    False → modules есть, либо зона неизвестна/корпусная → fallback как раньше.

Чистая функция, без вызова моделей и сети.
"""

from app.services.full_pipeline import is_no_door_zone_page


def test_kids_room_empty_modules_no_door():
    # Детская, кровать 2200×1400 (реальный кейс стр. 0008): fallback не нужен
    assert is_no_door_zone_page("Детская", []) is True


def test_bedroom_empty_modules_no_door():
    assert is_no_door_zone_page("Спальня", []) is True


def test_living_room_empty_modules_no_door():
    assert is_no_door_zone_page("Гостиная", []) is True


def test_hallway_empty_modules_no_door():
    assert is_no_door_zone_page("Прихожая", []) is True


def test_english_key_kids_room_no_door():
    # zone_type может прийти английским ключом (kids_room → Детская)
    assert is_no_door_zone_page("kids_room", []) is True


def test_kitchen_empty_modules_requires_fallback():
    # Кухня при пустых modules — как раньше: fallback + возможная ошибка
    assert is_no_door_zone_page("Кухня", []) is False


def test_bathroom_empty_modules_requires_fallback():
    assert is_no_door_zone_page("Ванная", []) is False


def test_wardrobe_empty_modules_requires_fallback():
    assert is_no_door_zone_page("Гардеробная", []) is False


def test_unknown_zone_empty_modules_requires_fallback():
    assert is_no_door_zone_page(None, []) is False
    assert is_no_door_zone_page("", []) is False
    assert is_no_door_zone_page("Какое-то помещение", []) is False


def test_modules_present_never_no_door():
    # Есть модули → гейт не срабатывает, даже в no-door-зоне
    fake_modules = ["lower_base"]
    assert is_no_door_zone_page("Детская", fake_modules) is False
    assert is_no_door_zone_page("Кухня", fake_modules) is False
