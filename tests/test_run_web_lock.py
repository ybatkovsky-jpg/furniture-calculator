"""Защита от второго запуска веб-сервера."""

from __future__ import annotations

from scripts import run_web


def test_second_web_instance_is_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_web, "LOCK_PATH", tmp_path / "web.lock")
    first = run_web._acquire_lock()
    assert first is not None
    try:
        assert run_web._acquire_lock() is None
    finally:
        first.close()
