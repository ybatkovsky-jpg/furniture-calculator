"""P1-13: сбой OCR не обнуляет весь прогон."""

import asyncio

from app.services import full_pipeline as fp_module
from app.services.full_pipeline import FullPipeline
from app.services.pdf_parser import PDFParseResult


def test_pipeline_continues_without_ocr(monkeypatch):
    pipeline = FullPipeline()

    async def failing_parse(_path):
        return PDFParseResult(success=False, error="GLM-OCR недоступен")

    async def fake_process_page(*_args, **_kwargs):
        return None

    pipeline.ocr.parse_pdf = failing_parse
    pipeline._process_page = fake_process_page
    monkeypatch.setattr(fp_module, "get_page_count", lambda _p: 4)

    result = asyncio.run(pipeline.process("dummy.pdf"))

    assert result.total_pages == 4
    assert any("OCR" in error for error in result.errors)
