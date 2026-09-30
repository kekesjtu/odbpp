"""Outline rectangularisation: exactness, holes, and the error it must report."""

from __future__ import annotations

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.rectify import (DEFAULT_TOLERANCE_MM, RectifyOptions,
                           _intervals, rectify_region)
from odbpp.clipper import region_from_rings


def region(*rings) -> "object":
    return region_from_rings([(points, is_hole) for points, is_hole in rings])


class TestRectify:
    def test_adjacent_staircase_bands_share_bit_identical_boundaries(self):
        result = rectify_region(region((
            [(36.5, 0), (256.5, 100), (256.5, 0)], False)),
            RectifyOptions(tolerance_mm=1.1155))
        xs = sorted({x for r in result.rectangles for x in (r.x0, r.x1)})
        assert all(b-a > 1e-6 for a, b in zip(xs, xs[1:]))

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

    def test_a_sub_tolerance_void_sliver_does_not_drop_the_slab(self):
        # A 0.001 mm slit, far below the 1 mm tolerance, cuts into the left half
        # of a solid.  Snapping the crossings before pairing them collapses the
        # two slit crossings onto one key, the key list becomes odd, and that
        # slab used to be dropped -- and then the neighbouring slab was stretched
        # over it, fabricating outline nobody measured.
        cut = [(0, 0), (20, 0), (20, 20), (10, 20), (10, 10.0005),
               (0, 10.0005), (0, 9.9995), (10, 9.9995), (10, 20), (0, 20)]
        result = rectify_region(region((cut, False)),
                                RectifyOptions(tolerance_mm=1.0))
        assert result.rectified_area_mm2 == pytest.approx(400.0, abs=1.0)
        assert result.lost_area_mm2 < 1.0
        assert result.ok

    def test_crossings_are_paired_before_they_are_snapped(self):
        # Pairing first keeps the solid/void reading of a sub-tolerance sliver:
        # the two inner crossings snap onto each other and merge the two solids
        # into one interval instead of leaving an odd key list behind.
        assert _intervals([0.0, 9.9995, 10.0005, 20.0], 1.0) == [(0, 20)]
        assert _intervals([0.0, 9.6, 10.4, 20.0], 1.0) == [(0, 20)]
        assert _intervals([1.0, 2.0, 3.0], 1.0) is None

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

    def test_a_loose_perimeter_bound_still_reports_the_area_moved(self):
        # The perimeter bound is the tolerance's own precision limit, so it can
        # be looser than the configured 1% relative limit.  A 45 degree
        # hypotenuse in an 80 mm square moves 40 mm2 of outline: more than the
        # 32 mm2 limit, well inside the 273 mm2 perimeter bound.  Accepted --
        # but a coarse tolerance must not move that much board area unnoticed.
        diagnostics = Diagnostics()
        result = rectify_region(region(([(0, 0), (80, 0), (80, 80)], False)),
                                RectifyOptions(tolerance_mm=1.0),
                                diagnostics=diagnostics)
        assert result.ok
        assert "rectify.area_warning" in diagnostics.codes()

        clean = Diagnostics()
        rectify_region(region(([(0, 0), (80, 0), (80, 80), (0, 80)], False)),
                       RectifyOptions(tolerance_mm=1.0), diagnostics=clean)
        assert "rectify.area_warning" not in clean.codes()
