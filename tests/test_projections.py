"""Выбор одной проекции на многоракурсном листе."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.projections import select_main_projection, split_clusters


def facade(x_pct: float, width_pct: float, width_mm: int) -> SimpleNamespace:
    return SimpleNamespace(
        bbox_x_pct=x_pct, bbox_w_pct=width_pct, width_mm=width_mm, zone="lower"
    )


def test_single_projection_is_untouched() -> None:
    facades = [facade(10, 15, 600), facade(27, 15, 600)]
    chosen, info = select_main_projection(facades, 1200)
    assert chosen == facades
    assert info["clusters"] == 1


def test_two_views_choose_one_matching_total() -> None:
    # Два вида по 2400 мм, габарит 2400 — берём любой один, но не оба.
    left = [facade(5, 10, 600), facade(16, 10, 600), facade(27, 10, 600), facade(38, 10, 600)]
    right = [facade(65, 10, 600), facade(76, 10, 600), facade(87, 10, 600), facade(98, 10, 600)]
    chosen, info = select_main_projection(left + right, 2400)
    assert info["clusters"] == 2
    assert len(chosen) == 4
    assert sum(f.width_mm for f in chosen) == 2400


def test_prefers_view_closer_to_dimension() -> None:
    small = [facade(5, 10, 300), facade(16, 10, 300)]           # 600 мм
    big = [facade(50, 10, 600), facade(61, 10, 600), facade(72, 10, 600)]  # 1800 мм
    chosen, _info = select_main_projection(small + big, 1800)
    assert sum(f.width_mm for f in chosen) == 1800


def test_without_dimension_takes_widest_view() -> None:
    small = [facade(5, 10, 300)]
    big = [facade(50, 10, 600), facade(61, 10, 600)]
    chosen, info = select_main_projection(small + big, None)
    assert sum(f.width_mm for f in chosen) == 1200
    assert "самая широкая" in info["reason"]


def test_split_clusters_merges_close_facades() -> None:
    facades = [facade(10, 20, 600), facade(31, 20, 600), facade(75, 20, 600)]
    clusters = split_clusters(facades)
    assert len(clusters) == 2
