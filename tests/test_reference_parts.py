"""Этап 3: Part-раскрой на реальных деталировках 4 проектов PRO100."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.nesting import nest_parts
from app.domain.parts import parts_area_m2, parts_from_details
from app.services.importing import ProjectImportService

PROJECTS = Path(__file__).resolve().parent / "fixtures" / "projects"
EXPECTED_AREA = {
    "pro100_project_a": 4.703,
    "pro100_project_b": 99.112,
    "pro100_project_c": 20.077,
    "pro100_project_d": 13.461,
}


@pytest.mark.parametrize(
    "project_dir",
    sorted(path for path in PROJECTS.iterdir() if path.is_dir()),
    ids=lambda path: path.name,
)
def test_parts_from_reference_details_nest(tmp_path: Path, project_dir: Path) -> None:
    project = asyncio.run(
        ProjectImportService(tmp_path / "projects").import_project(project_dir)
    )
    parts = parts_from_details(project.load_details())

    assert parts
    area = parts_area_m2(parts)
    expected = EXPECTED_AREA[project_dir.name]
    assert abs(area - expected) / expected <= 0.10
    nesting = nest_parts(parts)
    assert nesting.sheets_count > 0
    assert nesting.used_area_m2 > 0
