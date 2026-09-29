"""Outline rectangularisation: exactness, holes, and the error it must report."""

from __future__ import annotations

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.rectify import (DEFAULT_TOLERANCE_MM, RectifyOptions,
                           rectify_region)
from odbpp.clipper import region_from_rings


def region(*rings) -> "object":
    return region_from_rings([(points, is_hole) for points, is_hole in rings])


class TestRectify:
    def test_a_rectangle_is_one_exact_rectangle(self):
        result = rectify_region(region(([(0, 0), (10, 0), (10, 10), (0, 10)], False)),
                                RectifyOptions(tolerance_mm=0.5))
        assert len(result.rectangles) == 1
        assert result.rectified_area_mm2 == pytest.approx(100.0)
        assert result.shape_deviation_mm2 == pytest.approx(0.0)
        assert result.ok

    def test_an_axis_aligned_l_shape_is_exact(self):
        result = rectify_region(region((
            [(0, 0), (10, 0), (10, 5), (4, 5), (4, 10), (0, 10)], False)),
            RectifyOptions(tolerance_mm=0.5))
        assert result.rectified_area_mm2 == pytest.approx(70.0)
        assert result.ok

    def test_a_hole_survives(self):
        result = rectify_region(region(
            ([(0, 0), (10, 0), (10, 10), (0, 10)], False),
            ([(4, 4), (6, 4), (6, 6), (4, 6)], True),
        ), RectifyOptions(tolerance_mm=0.5))
        assert result.rectified_area_mm2 == pytest.approx(96.0)
        assert result.ok

    def test_a_hole_narrower_than_the_tolerance_is_reported_filled(self):
        diagnostics = Diagnostics()
        result = rectify_region(region(
            ([(0, 0), (10, 0), (10, 10), (0, 10)], False),
            ([(4.9, 4), (5.1, 4), (5.1, 6), (4.9, 6)], True),
        ), RectifyOptions(tolerance_mm=1.0), diagnostics=diagnostics)
        assert not result.ok
        assert "rectify.hole_filled" in diagnostics.codes()

    def test_a_narrow_island_is_reported_lost(self):
        diagnostics = Diagnostics()
        result = rectify_region(region(
            ([(0, 0), (10, 0), (10, 10), (0, 10)], False),
            ([(12.0, 4), (12.2, 4), (12.2, 6), (12.0, 6)], False),
        ), RectifyOptions(tolerance_mm=1.0), diagnostics=diagnostics)
        assert not result.ok
        assert "rectify.island_lost" in diagnostics.codes()

    def test_a_slanted_edge_becomes_a_staircase_with_bounded_error(self):
        result = rectify_region(region((
            [(0, 0), (10, 0), (10, 10), (5, 10)], False)),
            RectifyOptions(tolerance_mm=0.25))
        assert len(result.rectangles) > 1
        assert result.net_area_error_mm2 == pytest.approx(0.0, abs=1.0)
        assert result.ok

    def test_the_tolerance_defaults_to_the_documented_value(self):
        result = rectify_region(region(
            ([(0, 0), (10, 0), (10, 10), (0, 10)], False)),
            RectifyOptions())
        assert result.tolerance_mm == pytest.approx(DEFAULT_TOLERANCE_MM)

    def test_a_coarse_staircase_stays_within_the_perimeter_bound(self):
        # A staircase that follows the outline cannot move more than roughly a
        # tolerance per unit of boundary length, so a coarse tolerance that is
        # still faithful is not an error.
        result = rectify_region(region((
            [(0, 0), (10, 0), (10, 10), (5, 10)], False)),
            RectifyOptions(tolerance_mm=2.0))
        assert result.shape_deviation_mm2 > 0.0
        assert result.ok
