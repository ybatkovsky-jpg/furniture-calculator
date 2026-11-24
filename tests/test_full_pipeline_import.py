"""FullPipeline: обработка ImportedProject без повторного рендера PDF."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from app.services.full_pipeline import FullPipeline
from app.services.image_analyzer import RecognizedModule
from app.services.importing import ImportedPage, ImportedProject


def run(coro):
    return asyncio.run(coro)


class FakeVision:
    """Минимальная замена Vision: без сети и без модели."""

    async def analyze_page(self, image_path):
        module = RecognizedModule(
            type="lower_base",
            width=600,
            depth=560,
            height=820,
            quantity=1,
            facades={"count": 2},
            drawers={"count": 1},
        )
        return [module], "Кухня", ["ЛДСП"], "high"

    async def analyze_drawing(self, image_path):  # pragma: no cover - не должен вызываться
        raise AssertionError("fallback не нужен: analyze_page вернул модули")

    async def close(self):
        return None


def _project_with_image(tmp_path: Path) -> ImportedProject:
    image = tmp_path / "page.jpg"
    Image.new("RGB", (80, 60), "white").save(image)
    return ImportedProject(
        project_id="a" * 16,
        source_type="image",
        original_filename="план.jpg",
        pages=[
            ImportedPage(
                index=0,
                image_path=str(image),
                source_page=1,
                room_id="kitchen",
                room_name="Кухня",
                view_type="elevation",
                view_group="kitchen_main",
            )
        ],
    )


def test_process_imported_uses_page_images(tmp_path, monkeypatch) -> None:
    project = _project_with_image(tmp_path)
    pipeline = FullPipeline()
    pipeline.vision = FakeVision()

    def no_render(*args, **kwargs):  # pragma: no cover
        raise AssertionError("process_imported не должен рендерить PDF заново")

    monkeypatch.setattr("app.services.full_pipeline.render_page", no_render)

    result = run(pipeline.process_imported(project))

    assert result.success is True
    assert result.total_pages == 1
    assert len(result.rooms) == 1
    assert result.rooms[0].modules[0].width == 600
    assert result.rooms[0].confidence == "high"
    assert result.rooms[0].validation is not None
    assert result.rooms[0].validation["can_calculate"] is True
    assert result.rooms[0].validation["values"][0]["source_page"] == 1
    assert result.rooms[0].room_id == "kitchen"
    assert result.rooms[0].view_type == "elevation"
    assert result.rooms[0].view_group == "kitchen_main"


def test_process_imported_without_pages_returns_error() -> None:
    project = ImportedProject(
        project_id="b" * 16,
        source_type="pro100",
        original_filename="details.txt",
        pages=[],
    )
    result = run(FullPipeline().process_imported(project))

    assert result.success is False
    assert result.errors
    assert "страниц" in result.errors[0].lower()


def test_process_imported_marks_duplicate_projection(tmp_path) -> None:
    pages = []
    for index, view in enumerate(("elevation", "plan")):
        image = tmp_path / f"page-{index}.jpg"
        Image.new("RGB", (80, 60), "white").save(image)
        pages.append(
            ImportedPage(
                index=index,
                image_path=str(image),
                source_page=index + 1,
                room_id="kitchen",
                room_name="Кухня",
                view_type=view,
                view_group="kitchen_main",
            )
        )
    project = ImportedProject(
        project_id="c" * 16,
        source_type="pdf",
        original_filename="album.pdf",
        pages=pages,
    )
    pipeline = FullPipeline()
    pipeline.vision = FakeVision()

    result = run(pipeline.process_imported(project))

    assert len(result.rooms) == 2
    assert sum(1 for room in result.rooms if room.is_duplicate_view) == 1

