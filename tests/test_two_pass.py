"""Согласие двух проходов по составу (P3)."""

from __future__ import annotations

from app.services.two_pass import PassMetrics, accept_reask, agreement_ratio, consensus


def test_agreement_ratio_basic() -> None:
    assert agreement_ratio(15, 15) == 1.0
    assert agreement_ratio(16, 17) == 0.97
    assert agreement_ratio(0, 0) == 1.0
    assert agreement_ratio(0, 10) == 0.0


def test_close_passes_take_smaller_doors() -> None:
    result = consensus(PassMetrics(doors=16, modules=9), PassMetrics(doors=17, modules=4))
    assert result.doors == 16
    assert result.reliable is True
    assert any("детализация" in flag for flag in result.flags)


def test_divergent_passes_ask_for_reask() -> None:
    result = consensus(PassMetrics(doors=8, modules=4), PassMetrics(doors=15, modules=4))
    assert result.doors == 8
    assert result.reliable is False
    assert any("переспрос" in flag for flag in result.flags)


def test_no_doors_anywhere() -> None:
    result = consensus(PassMetrics(doors=0, modules=0), PassMetrics(doors=0, modules=0))
    assert result.doors == 0
    assert any("не найдены" in flag for flag in result.flags)


def test_modules_take_maximum() -> None:
    result = consensus(PassMetrics(doors=4, modules=2), PassMetrics(doors=4, modules=5))
    assert result.modules == 5
    assert result.reliable is True


def test_reask_is_accepted_when_close() -> None:
    ok, reason = accept_reask(consensus_doors=16, reask_doors=15)
    assert ok is True
    assert "в допуске" in reason


def test_reask_is_rejected_when_far() -> None:
    ok, reason = accept_reask(consensus_doors=16, reask_doors=27)
    assert ok is False
    assert "оставлен флаг" in reason


def test_reask_tolerance_grows_with_size() -> None:
    assert accept_reask(consensus_doors=4, reask_doors=6)[0] is True
    assert accept_reask(consensus_doors=30, reask_doors=34)[0] is True
    assert accept_reask(consensus_doors=30, reask_doors=39)[0] is False
