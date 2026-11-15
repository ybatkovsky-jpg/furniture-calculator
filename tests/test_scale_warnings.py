"""P2-06: коррекция ширины фасада не молчаливая."""

from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult, RecognizedModule
from app.services.scale_calc import calculate_scaled_facades


def test_scale_correction_sets_flag():
    narrow = calculate_scaled_facades(
        [{"zone": "lower", "bbox_x_pct": 0, "bbox_w_pct": 1}], 100
    )[0]
    wide = calculate_scaled_facades(
        [{"zone": "lower", "bbox_x_pct": 0, "bbox_w_pct": 1}], 3000
    )[0]
    assert narrow.corrected is True and narrow.width_mm == 250
    assert wide.corrected is True and wide.width_mm == 1000


def test_module_note_goes_to_result_notes():
    analyzer = GeminiImageAnalyzer()
    module = RecognizedModule(
        type="lower_base", width=250, depth=560, height=820,
        quantity=1, facades={"count": 1}, notes="Ширина скорректирована",
    )
    result = analyzer._postprocess_results(
        RecognitionResult(modules=[module], confidence="high")
    )
    assert "скорректирована" in (result.notes or "")
