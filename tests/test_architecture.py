"""P1-01: архитектурный контракт — в боте нет прямых вызовов расчётного ядра.

Смысл теста: если кто-то снова вызовет `calculate_full_cost` или
`calculate_cost` из обработчика, появится пятая точка пересчёта и заказ
опять начнёт давать разные суммы. Контракт проверяется по AST, а не grep'ом,
чтобы не ловить упоминания в комментариях/строках.
"""

import ast
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1] / "app" / "bot"
FORBIDDEN = {"calculate_full_cost", "calculate_cost"}


def _bot_modules() -> list[Path]:
    return sorted(BOT_DIR.rglob("*.py"))


def _used_calc_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith(("calc_engine", "cost_calc")):
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        found.add(alias.name)
    return found


def test_bot_does_not_call_calc_engine_directly():
    offenders = {}
    for path in _bot_modules():
        used = _used_calc_names(path)
        if used:
            offenders[str(path.relative_to(BOT_DIR.parent.parent))] = sorted(used)
    assert offenders == {}, (
        "Обработчики должны ходить через CalculationService, а не в движок: "
        f"{offenders}"
    )


def test_calculation_service_is_single_entry_point():
    """Сервис действительно вызывает ядро (а не пустая обёртка)."""
    service = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "calculation_service.py"
    )
    tree = ast.parse(service.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "calculate_full_cost" in called
