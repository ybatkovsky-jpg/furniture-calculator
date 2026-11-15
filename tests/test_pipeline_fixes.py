"""Регрессы P2-02/P2-03/P2-05 (пайплайн и постобработка)."""

from app.services.full_pipeline import FullPipeline
from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult, RecognizedModule
from app.services.pdf_parser import GLMOCRParser, PDFParseResult, ParsedRoom


def test_find_room_uses_page_field():
    pipeline = FullPipeline()
    result = PDFParseResult(
        success=True,
        rooms=[ParsedRoom(name="Кухня", page=2), ParsedRoom(name="Спальня", page=3)],
    )
    assert pipeline._find_room_for_page(result, 2).name == "Кухня"
    assert pipeline._find_room_for_page(result, 3).name == "Спальня"
    assert pipeline._find_room_for_page(result, 1) is None


def test_extract_notes_numbered_list():
    parser = GLMOCRParser()
    notes = parser._extract_notes("1. Проверить размеры\n2. Согласовать стекло")
    assert notes == ["Проверить размеры", "Согласовать стекло"]


def test_corner_duplicates_keep_quantity():
    """P2-05: два одинаковых угловых не должны схлопнуться в один."""
    analyzer = GeminiImageAnalyzer()
    corner = RecognizedModule(
        type="corner", width=900, depth=900, height=820,
        quantity=1, is_corner=True, facades={"count": 1},
    )
    result = RecognitionResult(modules=[corner, corner], confidence="high")
    processed = analyzer._postprocess_results(result)
    corners = [m for m in processed.modules if m.type == "corner"]
    assert len(corners) == 1
    assert corners[0].quantity == 2
