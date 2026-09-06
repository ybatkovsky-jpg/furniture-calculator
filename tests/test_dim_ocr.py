"""
Тесты OCR-заземления чисел размерных линий (app/services/dim_ocr.py).

Чистые функции без сети и бинарников:
- select_total_width — выбор числа-габарита из OCR-кандидатов;
- parse_glm_dimensions — устойчивый парсер ответа GLM-OCR (layout_details,
  prompt-JSON в md_results с markdown-обёрткой, regex-фолбэк).
"""

import pytest

from app.services.dim_ocr import (
    select_total_width,
    parse_glm_dimensions,
)


def _c(value, x=0.5, y=0.8, w=0.06, h=0.03):
    """Короткая запись кандидата {value, x, y, w, h} в долях изображения."""
    return {"value": value, "x": x, "y": y, "w": w, "h": h}


# ════════════════════════════════════════════════════════════════════
# select_total_width
# ════════════════════════════════════════════════════════════════════

def test_a_near_vlm_wide_chosen():
    """(а) Число близкое к vlm (±10%), широкая строка в нижних 2/3 → выбрано."""
    candidates = [
        _c(1581, x=0.45, y=0.75, w=0.08),   # близко к 1600, широкая строка, низ
        _c(3000, x=0.05, y=0.05, w=0.15),   # далеко от vlm, хоть и шире
    ]
    value, conf = select_total_width(candidates, 1600)
    assert value == 1581
    assert conf >= 0.7


def test_b_vlm_absent_central_wide_chosen():
    """(б) vlm бред (None/0) → выбрано самое центрально-широкое число."""
    candidates = [
        _c(670, x=0.02, y=0.85, w=0.03),    # узкое, у края
        _c(2400, x=0.47, y=0.80, w=0.12),   # широкое, в центре, внизу
        _c(905, x=0.90, y=0.50, w=0.04),    # узкое, у правого края
    ]
    for vlm in (None, 0):
        value, conf = select_total_width(candidates, vlm)
        assert value == 2400
        assert 0.0 <= conf <= 1.0


def test_c_no_candidates_returns_none():
    """(в) Кандидатов нет → (None, 0)."""
    assert select_total_width([], 3000) == (None, 0.0)
    assert select_total_width([], None) == (None, 0.0)
    assert select_total_width(None, 3000) == (None, 0.0)


def test_g_priority_of_closeness_to_vlm():
    """(г) Приоритет близости к vlm: близкое узкое бьёт далёкое широкое."""
    candidates = [
        _c(1620, x=0.50, y=0.80, w=0.02),   # близко к 1600, но узкое
        _c(3000, x=0.45, y=0.75, w=0.30),   # далеко, очень широкое
    ]
    value, conf = select_total_width(candidates, 1600)
    assert value == 1620
    assert conf >= 0.7


def test_strong_far_beats_weak_near():
    """VLM бредит (2581), близких сильных цифр нет → сильная цифра с
    размерной линии (широкая, центр, низ) побеждает с conf ≥ 0.7."""
    candidates = [
        _c(2490, x=0.03, y=0.50, w=0.02),   # близко к 2581, но у края (верт. размер)
        _c(1581, x=0.45, y=0.78, w=0.08),   # настоящий габарит: центр, низ, широкая
    ]
    value, conf = select_total_width(candidates, 2581)
    assert value == 1581
    assert conf >= 0.7


def test_weak_near_alone_low_confidence():
    """Близкий к vlm кандидат есть, но он слабый (край, узкий) →
    значение возвращается, но confidence < 0.7 (замены не будет)."""
    candidates = [_c(2490, x=0.03, y=0.50, w=0.02)]
    value, conf = select_total_width(candidates, 2581)
    assert value == 2490
    assert conf < 0.7


def test_near_vlm_no_replace_needed_same_value():
    """OCR совпадает с vlm (разница ≤ 10 мм) → возвращается то же значение."""
    candidates = [_c(1581, x=0.45, y=0.75, w=0.08)]
    value, conf = select_total_width(candidates, 1583)
    assert value == 1581
    assert conf >= 0.7


# ════════════════════════════════════════════════════════════════════
# parse_glm_dimensions — парсер ответа GLM-OCR
# ════════════════════════════════════════════════════════════════════

def test_parse_glm_layout_details():
    """layout_details: числа из контента элементов с bbox_2d [x0,y0,x1,y1]."""
    payload = {
        "md_results": "текст, который не нужен",
        "layout_details": [
            [
                {"label": "text", "content": "1830",
                 "bbox_2d": [0.40, 0.70, 0.50, 0.74]},
                {"label": "text", "content": "Профиль 3000K",
                 "bbox_2d": [0.05, 0.05, 0.20, 0.07]},
                {"label": "text", "content": "Egger H1379 ST36",
                 "bbox_2d": [0.60, 0.90, 0.75, 0.93]},
            ]
        ],
    }
    cands = parse_glm_dimensions(payload)
    values = sorted(c["value"] for c in cands)
    assert 1830 in values
    assert 3000 in values   # из «3000K»
    assert 1379 in values   # код материала — тоже число-кандидат
    assert all(0.0 <= c["x"] <= 1.0 for c in cands)
    assert all(0.0 <= c["y"] <= 1.0 for c in cands)
    c1830 = next(c for c in cands if c["value"] == 1830)
    assert abs(c1830["x"] - 0.40) < 1e-6
    assert abs(c1830["y"] - 0.70) < 1e-6
    assert abs(c1830["w"] - 0.10) < 1e-6   # x1 - x0
    assert abs(c1830["h"] - 0.04) < 1e-6   # y1 - y0


def test_parse_glm_md_json_markdown_wrapped():
    """md_results с markdown-обёрткой ```json ...``` → устойчивый парсинг."""
    payload = {
        "md_results": (
            "```json\n"
            '{"numbers": [{"value": 1581, "x": 0.5, "y": 0.85, "w": 0.1, "h": 0.02},'
            ' {"value": 2490, "x": 0.1, "y": 0.5}]}\n'
            "```"
        ),
    }
    cands = parse_glm_dimensions(payload)
    assert [c["value"] for c in cands] == [1581, 2490]
    assert abs(cands[0]["x"] - 0.5) < 1e-6
    assert abs(cands[0]["w"] - 0.1) < 1e-6
    assert abs(cands[1]["x"] - 0.1) < 1e-6
    assert abs(cands[1]["w"] - 0.08) < 1e-6   # дефолтная ширина


def test_parse_glm_json_with_prose_around():
    """JSON внутри прозы (как в реальных ответах VLM) → извлекается."""
    payload = {
        "md_results": (
            "Вот анализ чертежа:\n"
            '{"numbers": [{"value": 1830, "x": 0.4, "y": 0.8}]}\n'
            "Спасибо за внимание."
        ),
    }
    cands = parse_glm_dimensions(payload)
    assert [c["value"] for c in cands] == [1830]


def test_parse_glm_json_list_direct():
    """md_results — голый JSON-список записей."""
    payload = {"md_results": '[{"value": 3000, "x": 0.5, "y": 0.5}]'}
    cands = parse_glm_dimensions(payload)
    assert [c["value"] for c in cands] == [3000]


def test_parse_glm_plain_text_fallback():
    """Нет JSON — regex-фолбэк по тексту с примерным положением."""
    payload = {"md_results": "Ширина 1830 мм, высота 2493, глубина 350."}
    cands = parse_glm_dimensions(payload)
    values = sorted(c["value"] for c in cands)
    assert values == [350, 1830, 2493]
    for c in cands:
        assert 0.0 <= c["x"] <= 1.0 and 0.0 <= c["y"] <= 1.0


def test_parse_glm_filters_out_of_range():
    """Только числа 200..8000; 199/8001/двузначные отбрасываются."""
    payload = {"md_results": "числа: 16 199 8001 3000"}
    values = [c["value"] for c in parse_glm_dimensions(payload)]
    assert values == [3000]


def test_parse_glm_pixel_bbox_normalized():
    """bbox в пикселях (с width/height элемента) → нормализация к 0..1."""
    payload = {
        "layout_details": [[
            {"label": "text", "content": "3000",
             "bbox_2d": [400, 700, 500, 740],
             "width": 1000, "height": 1000},
        ]],
    }
    cands = parse_glm_dimensions(payload)
    assert len(cands) == 1
    c = cands[0]
    assert c["value"] == 3000
    assert abs(c["x"] - 0.4) < 1e-6
    assert abs(c["y"] - 0.7) < 1e-6
    assert abs(c["w"] - 0.1) < 1e-6


def test_parse_glm_percent_coords():
    """Координаты в процентах (50 вместо 0.5) без размеров изображения."""
    payload = {"md_results": '{"numbers": [{"value": 2400, "x": 50, "y": 80}]}'}
    cands = parse_glm_dimensions(payload)
    assert len(cands) == 1
    assert abs(cands[0]["x"] - 0.5) < 1e-6
    assert abs(cands[0]["y"] - 0.8) < 1e-6


def test_parse_glm_empty():
    """Пустой/невалидный ответ → []. Ввод не-словарь → []."""
    assert parse_glm_dimensions({}) == []
    assert parse_glm_dimensions({"md_results": ""}) == []
    assert parse_glm_dimensions(None) == []
    assert parse_glm_dimensions("строка вместо dict") == []
