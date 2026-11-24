"""Эталонные проекты: реальные выгрузки ПРО100 как регрессионный вход.

Это входные данные четырёх проектов и ожидаемый разбор «Списка деталей».
BOM, раскрой и цена для них ещё требуют подтверждения технолога (этап 0.5).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.importing import ProjectImportService

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROJECTS = FIXTURES / "projects"
EXPECTED = FIXTURES / "expected"


@pytest.mark.parametrize(
    "project_dir",
    sorted(path for path in PROJECTS.iterdir() if path.is_dir()),
    ids=lambda path: path.name,
)
def test_pro100_reference_fixture_parses_details(
    tmp_path: Path, project_dir: Path
) -> None:
    expected = json.loads(
        (EXPECTED / f"{project_dir.name}.json").read_text(encoding="utf-8")
    )
    project = asyncio.run(
        ProjectImportService(tmp_path / "projects").import_project(project_dir)
    )

    assert project.source_type == "pro100"
    assert project.metadata["details_count"] == expected["details_count"]
    assert project.metadata["panels_count"] == expected["panels_count"]

    details = project.load_details()
    assert len(details) == expected["details_count"]
    first = details[0]
    for key, value in expected["first_detail"].items():
        assert first[key] == value
    assert project.metadata["details_path"].endswith("details.json")
