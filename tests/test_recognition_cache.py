"""
Тесты кэша признания recognition_cache (без сети; CACHE_DIR подменяется tmp_path).

Покрывают:
- image_key / prompt_version (детерминированность, зависимость от контента);
- save/load roundtrip и структуру записи;
- approve (подтверждение записи);
- битый JSON → трактуем как промах и перезаписываем;
- смена текста промпта меняет файл кэша;
- сериализация RecognizedModule ↔ dict.
"""

import json

import pytest

from app.services import recognition_cache


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(recognition_cache, "CACHE_DIR", tmp_path)
    return tmp_path


def _make_image(cache_dir, name="img.jpg", content=b"furniture drawing bytes"):
    p = cache_dir / name
    p.write_bytes(content)
    return p


# ════════════════════════════════════════════════════════════════
# КЛЮЧИ
# ════════════════════════════════════════════════════════════════

def test_image_key_deterministic_and_content_based(cache_dir):
    img = _make_image(cache_dir)
    assert recognition_cache.image_key(img) == recognition_cache.image_key(img)
    img2 = _make_image(cache_dir, "img2.jpg", b"other bytes")
    assert recognition_cache.image_key(img) != recognition_cache.image_key(img2)
    assert len(recognition_cache.image_key(img)) == 16


def test_prompt_version_changes_with_text():
    v1 = recognition_cache.prompt_version("Промпт A")
    v2 = recognition_cache.prompt_version("Промпт B")
    assert v1 != v2
    assert len(v1) == 8
    assert recognition_cache.prompt_version("Промпт A") == v1


# ════════════════════════════════════════════════════════════════
# SAVE / LOAD / APPROVE
# ════════════════════════════════════════════════════════════════

def test_save_load_roundtrip(cache_dir):
    img = _make_image(cache_dir)
    data = {
        "modules": [],
        "zone_type": "Кухня",
        "materials": ["EGGER H1379"],
        "confidence": "high",
        "total_width_mm": 1800,
        "scale_ok": True,
        "door_count": 3,
    }
    recognition_cache.save(img, "PROMPT", data, approved=False, attempts=2)

    loaded = recognition_cache.load(img, "PROMPT")
    assert loaded is not None
    assert loaded["data"] == data
    assert loaded["approved"] is False
    assert loaded["attempts"] == 2
    assert loaded["image_key"] == recognition_cache.image_key(img)
    assert loaded["prompt_version"] == recognition_cache.prompt_version("PROMPT")
    assert isinstance(loaded["saved_at"], str)


def test_load_miss_returns_none(cache_dir):
    img = _make_image(cache_dir)
    assert recognition_cache.load(img, "PROMPT") is None


def test_different_prompt_is_a_miss(cache_dir):
    img = _make_image(cache_dir)
    recognition_cache.save(img, "PROMPT-A", {"modules": []}, approved=True, attempts=1)
    assert recognition_cache.load(img, "PROMPT-B") is None


def test_corrupted_json_treated_as_miss_and_rewritten(cache_dir):
    img = _make_image(cache_dir)
    path = cache_dir / (
        f"{recognition_cache.image_key(img)}_"
        f"{recognition_cache.prompt_version('P')}.json"
    )
    path.write_text("{битый json", encoding="utf-8")

    assert recognition_cache.load(img, "P") is None

    # save() не падает и перезаписывает битый файл валидной записью
    recognition_cache.save(img, "P", {"modules": []}, approved=True, attempts=1)
    loaded = recognition_cache.load(img, "P")
    assert loaded is not None
    assert loaded["approved"] is True


def test_corrupted_json_not_a_dict_is_a_miss(cache_dir):
    img = _make_image(cache_dir)
    path = cache_dir / (
        f"{recognition_cache.image_key(img)}_"
        f"{recognition_cache.prompt_version('P')}.json"
    )
    path.write_text(json.dumps(["не объект"]), encoding="utf-8")
    assert recognition_cache.load(img, "P") is None


def test_approve_flips_flag(cache_dir):
    img = _make_image(cache_dir)
    recognition_cache.save(img, "P", {"modules": []}, approved=False, attempts=1)

    updated = recognition_cache.approve(img, "P")
    assert updated["approved"] is True
    assert recognition_cache.load(img, "P")["approved"] is True


def test_approve_on_missing_returns_none(cache_dir):
    img = _make_image(cache_dir)
    assert recognition_cache.approve(img, "P") is None


# ════════════════════════════════════════════════════════════════
# СЕРИАЛИЗАЦИЯ МОДУЛЕЙ
# ════════════════════════════════════════════════════════════════

def test_module_roundtrip():
    from app.services.image_analyzer import RecognizedModule

    m = RecognizedModule(
        type="lower_base", width=600, depth=560, height=716,
        quantity=2, has_glass=True,
        facades={"count": 1, "type": "doors"},
        drawers={"count": 1},
        shelves=1, is_corner=False,
        bbox={"x": 1, "y": 2, "w": 100, "h": 200},
    )
    d = recognition_cache.module_to_dict(m)
    assert isinstance(d, dict)
    assert d["facades"] == {"count": 1, "type": "doors"}

    restored = recognition_cache.dict_to_module(d)
    assert restored == m


def test_dict_to_module_drops_extra_keys():
    d = {
        "type": "upper_base", "width": 600, "depth": 320, "height": 596,
        "quantity": 1, "extra_field": "мусор",
    }
    m = recognition_cache.dict_to_module(d)
    assert m.type == "upper_base"
    assert not hasattr(m, "extra_field")


def test_dict_to_module_missing_required_raises():
    with pytest.raises(ValueError):
        recognition_cache.dict_to_module({"type": "lower_base"})


def test_dicts_to_modules_skips_broken_entries():
    from app.services.image_analyzer import RecognizedModule

    good = {
        "type": "lower_base", "width": 600, "depth": 560, "height": 716, "quantity": 1,
    }
    mods = recognition_cache.dicts_to_modules([good, {"type": "x"}, "not-a-dict"])
    assert len(mods) == 1
    assert isinstance(mods[0], RecognizedModule)


def test_save_with_real_modules_roundtrip(cache_dir):
    """data.modules из реальных RecognizedModule переживает save/load."""
    from app.services.image_analyzer import RecognizedModule

    img = _make_image(cache_dir)
    mods = [
        RecognizedModule(type="lower_base", width=600, depth=560, height=716, quantity=2),
        RecognizedModule(type="upper_base", width=600, depth=320, height=596),
    ]
    data = {
        "modules": [recognition_cache.module_to_dict(m) for m in mods],
        "zone_type": "Кухня",
        "materials": [],
        "confidence": "high",
        "total_width_mm": 1800,
        "scale_ok": True,
        "door_count": 3,
    }
    recognition_cache.save(img, "PROMPT", data, approved=True, attempts=1)

    loaded = recognition_cache.load(img, "PROMPT")
    restored = recognition_cache.dicts_to_modules(loaded["data"]["modules"])
    assert restored == mods
