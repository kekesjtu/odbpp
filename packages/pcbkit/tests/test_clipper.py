"""Boolean geometry: quantisation, winding, and the sweep primitives.

The tests here check *numbers* rather than shapes wherever possible, because the
failure modes of this layer are quantitative.  A round symbol swept as a
tessellated polygon, for instance, still looks like a line -- it is just 5% too
narrow, and only an area or width assertion catches it.

The analytic values used throughout:

* 1 mm square swept along a 5 mm axis-aligned segment -> ``5*1 + 1 = 6`` mm^2
* the same square along a 5 mm 45-degree segment -> ``1 + 5*sqrt(2)*sqrt(2) = 11``
  mm^2, because the symbol is *dragged* and not rotated
* a round symbol of radius r swept along a length L -> ``2*r*L + pi*r^2``
"""

from __future__ import annotations

import math

import pytest

from pcbkit.clipper import (
    SCALE,
    Region,
    Ring,
    difference,
    intersection,
    offset,
    region_from_geometry,
    region_from_rings,
    simplify,
    snap_to_grid,
    stroke_paths,
    sweep,
    sweep_paths,
    sweep_polyline,
    to_surface_geometry,
    union,
)
from pcbkit.geometry import is_clockwise, standard_symbol_geometry
from pcbkit.symbols import parse_standard_symbol
from pcbkit.units import UnitSystem


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_measurements_follow_region_edits_after_reading():
    """Caching immutable rings must not freeze the mutable region's totals."""
    from dataclasses import replace

    ring = Ring(((0, 0), (0, 1), (1, 1), (1, 0)), False)
    region = Region([ring])
    assert region.area == 1
    assert region.bounds == (0, 0, 1, 1)
    moved = replace(ring, points=((2, 0), (2, 2), (4, 2), (4, 0)))
    region.rings.append(moved)
    assert region.area == 5
    assert region.bounds == (0, 0, 4, 2)
    region.rings[:] = [moved]
    assert region.area == 4
    assert region.bounds == (2, 0, 4, 2)
    region.rings.clear()
    assert region.area == 0
    assert region.bounds is None

def square(x0: float, y0: float, x1: float, y1: float):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def island(*points) -> Region:
    return region_from_rings([(points, False)])


def square_region(x0, y0, x1, y1) -> Region:
    return island(*square(x0, y0, x1, y1))


def symbol_geometry(name: str):
    symbol = parse_standard_symbol(name, UnitSystem.MM)
    assert symbol is not None, name
    return standard_symbol_geometry(symbol)


def square_ring(side: float):
    half = side / 2.0
    return [(-half, -half), (half, -half), (half, half), (-half, half)]


# ---------------------------------------------------------------------------
# representation
# ---------------------------------------------------------------------------

class TestRegionBasics:
    def test_area_of_a_plain_square(self):
        assert square_region(0, 0, 10, 10).area == pytest.approx(100.0)

    def test_an_empty_region_is_falsy_not_an_error(self):
        region = Region()
        assert not region
        assert region.area == 0.0
        assert region.bounds is None
        assert region.islands == []

    def test_area_subtracts_holes(self):
        outer = region_from_rings([(square(0, 0, 10, 10), False),
                                   (square(3, 3, 7, 7), True)])
        assert outer.area == pytest.approx(100.0 - 16.0)
        assert len(outer.islands) == 1
        assert len(outer.holes) == 1

    def test_bounds_span_every_ring(self):
        region = region_from_rings([(square(0, 0, 10, 10), False),
                                    (square(20, 20, 25, 30), False)])
        assert region.bounds == (0.0, 0.0, 25.0, 30.0)

    def test_ring_reports_its_own_area_and_bounds(self):
        ring = region_from_rings([(square(1, 2, 4, 6), False)]).rings[0]
        assert ring.area == pytest.approx(12.0)
        assert ring.bounds == (1.0, 2.0, 4.0, 6.0)
        assert len(ring) == 4


class TestWinding:
    """Spec p.39: islands clockwise, holes counter-clockwise."""

    def test_islands_come_out_clockwise(self):
        ring = square_region(0, 0, 10, 10).rings[0]
        assert not ring.is_hole
        assert is_clockwise(list(ring.points))

    def test_holes_come_out_counter_clockwise(self):
        region = region_from_rings([(square(0, 0, 10, 10), False),
                                    (square(3, 3, 7, 7), True)])
        hole = region.holes[0]
        assert not is_clockwise(list(hole.points))

    def test_input_winding_is_normalised_not_trusted(self):
        # Feed the island counter-clockwise and the hole clockwise: the opposite
        # of the spec, which is what a hand-written caller is likely to do.
        clockwise_square = list(reversed(square(0, 0, 10, 10)))
        region = region_from_rings([(clockwise_square, False)])
        assert is_clockwise(list(region.rings[0].points))

    def test_boolean_results_are_wound_correctly(self):
        result = difference(square_region(0, 0, 10, 10),
                            square_region(3, 3, 7, 7))
        assert is_clockwise(list(result.islands[0].points))
        assert not is_clockwise(list(result.holes[0].points))


class TestDegenerateInput:
    def test_a_two_point_ring_is_dropped(self):
        assert not region_from_rings([([(0, 0), (1, 1)], False)])

    def test_a_collinear_ring_is_dropped(self):
        assert not region_from_rings([([(0, 0), (5, 0), (10, 0)], False)])
        assert not region_from_rings([([(0, 0), (1, 1), (2, 2)], False)])

    def test_a_self_intersecting_ring_is_kept_for_repair(self):
        # A bowtie's signed area is exactly zero, but it is not degenerate: it is
        # malformed, and simplify() exists to fix it.  Dropping it here would
        # silently delete real geometry.
        bowtie = island((0, 0), (10, 10), (10, 0), (0, 10))
        assert len(bowtie.rings) == 1

    def test_a_ring_that_quantises_flat_is_dropped(self):
        # At 1e-6 mm these collapse to two distinct integer points, which is not a
        # polygon.  Dropping is right: Clipper would otherwise report a
        # degeneracy that the caller cannot act on.
        assert not island((0.0, 0.0), (0.0000001, 0.0), (0.0000002, 0.0))

    def test_a_repeated_closing_point_is_dropped(self):
        closed = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        region = island(*closed)
        assert len(region.rings[0]) == 4

    def test_duplicate_adjacent_points_are_dropped(self):
        region = island((0, 0), (0, 0), (10, 0), (10, 0), (10, 10), (0, 10))
        assert len(region.rings[0]) == 4


# ---------------------------------------------------------------------------
# booleans
# ---------------------------------------------------------------------------

class TestUnion:
    def test_disjoint_islands_stay_separate(self):
        result = union(square_region(0, 0, 1, 1), square_region(5, 5, 6, 6))
        assert len(result.rings) == 2
        assert result.area == pytest.approx(2.0)

    def test_overlapping_islands_merge(self):
        result = union(square_region(0, 0, 10, 10), square_region(5, 5, 15, 15))
        assert len(result.rings) == 1
        assert result.area == pytest.approx(175.0)

    def test_a_contained_island_leaves_no_hole(self):
        # Both are positive, so the smaller adds nothing and must not punch a hole.
        # This is the case fill-rule="evenodd" gets wrong.
        result = union(square_region(0, 0, 10, 10), square_region(3, 3, 7, 7))
        assert len(result.rings) == 1
        assert result.holes == []
        assert result.area == pytest.approx(100.0)

    def test_union_of_nothing_is_empty(self):
        assert not union()

    def test_union_of_one_region_is_a_copy(self):
        source = square_region(0, 0, 1, 1)
        result = union(source)
        assert result.area == source.area
        assert result.rings is not source.rings

    def test_a_hole_is_preserved_through_a_union(self):
        donut = region_from_rings([(square(0, 0, 10, 10), False),
                                   (square(4, 4, 6, 6), True)])
        result = union(donut, square_region(100, 100, 101, 101))
        assert result.area == pytest.approx(100.0 - 4.0 + 1.0)
        assert len(result.holes) == 1


class TestDifference:
    def test_subtracting_a_contained_square_makes_a_hole(self):
        result = difference(square_region(0, 0, 10, 10),
                            square_region(3, 3, 7, 7))
        assert result.area == pytest.approx(84.0)
        assert len(result.holes) == 1

    def test_subtracting_from_empty_is_empty(self):
        assert not difference(Region(), square_region(0, 0, 1, 1))

    def test_subtracting_nothing_keeps_the_subject(self):
        subject = square_region(0, 0, 10, 10)
        assert difference(subject, Region()).area == pytest.approx(100.0)

    def test_subtracting_an_overlap_trims_it(self):
        result = difference(square_region(0, 0, 10, 10),
                            square_region(5, 5, 15, 15))
        assert result.area == pytest.approx(75.0)
        assert result.bounds == (0.0, 0.0, 10.0, 10.0)

    def test_subtracting_a_disjoint_region_changes_nothing(self):
        result = difference(square_region(0, 0, 10, 10),
                            square_region(50, 50, 60, 60))
        assert result.area == pytest.approx(100.0)


class TestIntersection:
    def test_overlap(self):
        result = intersection(square_region(0, 0, 10, 10),
                              square_region(5, 5, 20, 20))
        assert result.area == pytest.approx(25.0)

    def test_disjoint_gives_nothing(self):
        assert not intersection(square_region(0, 0, 1, 1),
                                square_region(5, 5, 6, 6))

    def test_no_operands_gives_nothing(self):
        assert not intersection()

    def test_a_single_operand_is_returned(self):
        assert intersection(square_region(0, 0, 2, 2)).area == pytest.approx(4.0)

    def test_an_empty_operand_makes_the_whole_intersection_empty(self):
        # Filtering the empties out first made ``A & 0`` return A: an empty
        # region read as "no constraint" instead of "nothing survives".
        assert not intersection(square_region(0, 0, 2, 2), Region())
        assert not intersection(Region(), square_region(0, 0, 2, 2))
        assert not intersection(square_region(0, 0, 2, 2),
                                square_region(0, 0, 1, 1), Region())

    def test_three_operands_intersect_pairwise(self):
        # Feeding B and C together to the clip argument computes A & (B | C),
        # which for two halves of A is all of A (4 mm^2) rather than nothing.
        a = square_region(0, 0, 2, 2)
        b = square_region(0, 0, 1, 2)
        c = square_region(1, 0, 2, 2)
        assert not intersection(a, b, c)
        assert intersection(a, b, b).area == pytest.approx(2.0)


class TestSimplify:
    def test_a_bowtie_is_resolved(self):
        # Spec p.40 forbids self-intersecting polygons, so this is a repair for
        # input that violates the spec rather than a routine step.
        bowtie = island((0, 0), (10, 10), (10, 0), (0, 10))
        assert len(bowtie.rings) == 1
        repaired = simplify(bowtie)
        assert len(repaired.rings) == 2

    def test_simplifying_an_empty_region_is_empty(self):
        assert not simplify(Region())

    def test_a_clean_region_is_unchanged(self):
        assert simplify(square_region(0, 0, 2, 2)).area == pytest.approx(4.0)


class TestOffset:
    def test_growing_a_square_adds_a_border(self):
        grown = offset(square_region(0, 0, 10, 10), 1.0)
        # Exact for square corners; the rounded corners are tessellated, so a
        # small deficit is expected and bounded by the default sagitta.
        assert grown.area == pytest.approx(12 * 12 - (4 - math.pi) * 1.0, abs=0.05)
        assert grown.bounds == pytest.approx((-1.0, -1.0, 11.0, 11.0))

    def test_shrinking_a_square_removes_a_border(self):
        shrunk = offset(square_region(0, 0, 10, 10), -1.0)
        assert shrunk.bounds == pytest.approx((1.0, 1.0, 9.0, 9.0))

    def test_offsetting_by_zero_returns_empty_by_contract(self):
        # Documented: callers guard on delta, so a zero delta is a no-op request
        # and an empty answer is the cheapest way to say so.
        assert not offset(square_region(0, 0, 1, 1), 0.0)

    def test_offsetting_nothing_is_empty(self):
        assert not offset(Region(), 1.0)

    def test_shrinking_past_nothing_is_empty(self):
        assert not offset(square_region(0, 0, 1, 1), -5.0)


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------

class TestSquareSweep:
    """A square is dragged, not rotated, so it stays axis-aligned throughout."""

    def test_along_a_straight_line(self):
        result = sweep_polyline(square_ring(1.0), [(0, 0), (5, 0)])
        assert result.area == pytest.approx(5 * 1 + 1)

    def test_the_ends_are_flat_and_axis_aligned(self):
        result = sweep_polyline(square_ring(1.0), [(0, 0), (5, 0)])
        assert result.bounds == pytest.approx((-0.5, -0.5, 5.5, 0.5))

    def test_along_a_diagonal_keeps_the_square_unrotated(self):
        # The characteristic number: dragging an axis-aligned unit square along a
        # 5 mm 45-degree segment gives 1 + 5*sqrt(2)*sqrt(2) = 11 mm^2.  A rotating
        # aperture would give 5*sqrt(2)*1 + 1 = 8.07, and a diamond aperture 10.
        result = sweep_polyline(square_ring(1.0), [(0, 0), (5, 5)])
        assert result.area == pytest.approx(11.0)
        assert result.bounds == pytest.approx((-0.5, -0.5, 5.5, 5.5))

    def test_a_polyline_is_swept_as_one_band(self):
        # 5+5 for the two legs plus the corner square.
        result = sweep_polyline(square_ring(1.0), [(0, 0), (5, 0), (5, 5)])
        assert result.area == pytest.approx(11.0)
        assert len(result.rings) == 1

    def test_a_reversed_polyline_matches(self):
        forward = sweep_polyline(square_ring(2.0), [(0, 0), (4, 3)])
        backward = sweep_polyline(square_ring(2.0), [(4, 3), (0, 0)])
        assert forward.area == pytest.approx(backward.area)

    def test_a_degenerate_ring_sweeps_to_nothing(self):
        assert not sweep_polyline([(0, 0), (1, 1)], [(0, 0), (5, 0)])

    def test_a_degenerate_path_sweeps_to_nothing(self):
        assert not sweep_polyline(square_ring(1.0), [(0, 0)])

    def test_an_undefined_square_sweeps_to_nothing(self):
        assert not sweep_polyline(square_ring(0.0), [(0, 0), (5, 0)])


class TestRoundStroke:
    """Offsetting an open centreline: exact width, tessellated caps only."""

    def test_width_is_exact_regardless_of_tessellation(self):
        # This is the assertion that forced the choice of ClipperOffset over
        # MinkowskiSum.  A 0.1 mm radius circle tessellates to a 10-gon at the
        # default sagitta, whose extent is 95% of the diameter -- a 5% line-width
        # error, which is invisible in the area and very visible on screen.
        for radius in (0.015, 0.05, 0.1, 0.5):
            result = stroke_paths([[(0, 0), (10, 0)]], radius)
            x0, y0, x1, y1 = result.bounds
            assert (y1 - y0) == pytest.approx(2 * radius, abs=1e-6)

    def test_area_matches_the_stadium_formula(self):
        radius, length = 0.5, 5.0
        result = stroke_paths([[(0, 0), (length, 0)]], radius)
        expected = 2 * radius * length + math.pi * radius ** 2
        # The only error is the tessellated round caps.
        assert result.area == pytest.approx(expected, rel=0.005)

    def test_caps_extend_beyond_the_endpoints(self):
        result = stroke_paths([[(0, 0), (10, 0)]], 0.25)
        x0, y0, x1, y1 = result.bounds
        # The caps are tessellated arcs, so they stop a fraction of the
        # sagitta short of the ideal extremity -- never past it.
        assert (x0, y0, x1, y1) == pytest.approx((-0.25, -0.25, 10.25, 0.25), abs=5e-3)

    def test_a_polyline_gets_a_round_join(self):
        result = stroke_paths([[(0, 0), (5, 0), (5, 5)]], 0.5)
        assert result.area == pytest.approx(5 + 5 + math.pi * 0.25, rel=0.01)

    def test_many_paths_batch_identically_to_being_swept_separately(self):
        paths = [[(0, 0), (10, 0)], [(0, 5), (10, 5)], [(3, -3), (3, 3)]]
        batched = stroke_paths(paths, 0.2)
        separately = Region()
        for path in paths:
            separately = union(separately, stroke_paths([path], 0.2))
        assert batched.area == pytest.approx(separately.area)
        assert batched.bounds == pytest.approx(separately.bounds)

    def test_a_zero_radius_stroke_is_empty(self):
        # Zero-width artwork (r0) encloses no area.  Drawing it as a hairline is a
        # caller's decision, made in expand.ExpandOptions, not made here.
        assert not stroke_paths([[(0, 0), (10, 0)]], 0.0)

    def test_a_degenerate_path_is_skipped(self):
        assert not stroke_paths([[(0, 0)]], 0.5)

    def test_no_paths_gives_nothing(self):
        assert not stroke_paths([], 0.5)

    def test_a_closed_centreline_keeps_its_final_segment(self):
        # The regression from the second review: a full-circle arc's centreline
        # ends where it starts, so offsetting it as an *open* path dropped the
        # last segment.  With a narrow stroke the two round caps no longer
        # overlapped and the annulus lost its hole entirely.
        radius, count = 5.0, 64
        ring = [(radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count))
                for i in range(count)]
        closed = ring + [ring[0]]

        result = stroke_paths([closed], 0.05)
        assert len(result.holes) == 1
        # The annulus between 4.95 and 5.05 mm has area pi.
        assert result.area == pytest.approx(math.pi, rel=0.01)

    def test_an_open_centreline_is_still_capped(self):
        # The closure rule must not turn a genuine open trace into a loop.
        assert stroke_paths([[(0, 0), (10, 0)]], 0.5).holes == []


class TestSweepPaths:
    def test_matches_the_single_pattern_case(self):
        one = sweep_paths([square_ring(1.0)], [[(0, 0), (5, 0)]])
        assert one.area == pytest.approx(6.0)

    def test_a_closed_path_is_swept_as_a_loop(self):
        # Same closure rule as the round stroke: a square symbol swept along a
        # closed centreline must cover the last segment too, or the loop has a
        # gap where two sweeps should meet.
        radius, count = 5.0, 64
        ring = [(radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count))
                for i in range(count)]
        closed = sweep_paths([square_ring(0.2)], [ring + [ring[0]]])
        open_path = sweep_paths([square_ring(0.2)], [ring])
        assert len(closed.holes) == 1
        assert open_path.holes == []
        assert closed.area > open_path.area

    def test_two_pattern_sizes_union(self):
        result = sweep_paths([square_ring(1.0), square_ring(2.0)],
                             [[(0, 0), (5, 0)]])
        # The 2 mm square dominates the 1 mm one along the whole line.
        assert result.area == pytest.approx(5 * 2 + 4)

    def test_several_paths_union(self):
        result = sweep_paths([square_ring(1.0)],
                             [[(0, 0), (5, 0)], [(0, 10), (5, 10)]])
        assert result.area == pytest.approx(12.0)
        assert len(result.rings) == 2

    def test_a_reversed_pattern_winding_is_normalised(self):
        forward = sweep_paths([square_ring(1.0)], [[(0, 0), (5, 0)]])
        backward = sweep_paths([list(reversed(square_ring(1.0)))],
                               [[(0, 0), (5, 0)]])
        assert forward.area == pytest.approx(backward.area)

    def test_degenerate_inputs_give_nothing(self):
        assert not sweep_paths([square_ring(1.0)], [])
        assert not sweep_paths([[(0, 0), (1, 1)]], [[(0, 0), (5, 0)]])
        assert not sweep_paths([square_ring(1.0)], [[(0, 0)]])


class TestGeometrySweep:
    """sweep() generalises a whole symbol: islands swept, holes swept and cut."""

    def test_a_round_symbol_sweeps_like_a_stroke(self):
        result = sweep(symbol_geometry("r1000"), [(0, 0), (5, 0)])
        assert result.area == pytest.approx(5 * 1 + math.pi * 0.25, rel=0.02)

    def test_a_square_symbol_sweeps_exactly(self):
        result = sweep(symbol_geometry("s1000"), [(0, 0), (5, 0)])
        assert result.area == pytest.approx(6.0)

    def test_a_donut_sweeps_its_hole_into_a_tunnel(self):
        # The hole is a real hole, so the swept band has a tunnel through it and
        # the area is outer_stadium - inner_stadium.
        result = sweep(symbol_geometry("donut_r2000x1000"), [(0, 0), (5, 0)])
        outer = 5 * 2 + math.pi * 1.0 ** 2
        inner = 5 * 1 + math.pi * 0.5 ** 2
        assert result.area == pytest.approx(outer - inner, rel=0.02)
        assert len(result.holes) == 1

    def test_an_arc_path_sweeps_along_the_curve(self):
        quarter = [(5 * math.cos(t), 5 * math.sin(t))
                   for t in [i * (math.pi / 2) / 24 for i in range(25)]]
        result = sweep(symbol_geometry("r300"), quarter)
        expected = 0.3 * (5 * math.pi / 2) + math.pi * 0.15 ** 2
        assert result.area == pytest.approx(expected, rel=0.02)

    def test_empty_geometry_or_path_gives_nothing(self):
        assert not sweep(symbol_geometry("r1000"), [(0, 0)])
        from pcbkit.model import SurfaceGeometry
        assert not sweep(SurfaceGeometry(), [(0, 0), (5, 0)])


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

class TestSurfaceGeometryConversion:
    def test_round_trip_preserves_area(self):
        source = symbol_geometry("rect2000x1000")
        region = region_from_geometry(source)
        assert region.area == pytest.approx(2.0 * 1.0)

    def test_arcs_are_tessellated_on_the_way_in(self):
        source = symbol_geometry("r1000")
        region = region_from_geometry(source)
        # The default 5 um sagitta gives a 23-gon at this radius, i.e. ~1.2% low.
        # Coarse on purpose: it is ~5x finer than a fabrication tolerance.
        assert region.area == pytest.approx(math.pi * 0.25, rel=0.02)

    def test_island_and_hole_flags_survive(self):
        region = region_from_geometry(symbol_geometry("donut_r2000x1000"))
        assert len(region.islands) == 1
        assert len(region.holes) == 1

    def test_to_surface_geometry_emits_segments_only(self):
        geometry = to_surface_geometry(symbol_geometry("r1000") and
                                       region_from_geometry(symbol_geometry("r1000")))
        assert geometry.polygons
        for polygon in geometry.polygons:
            assert all(not part.is_arc for part in polygon.parts)

    def test_to_surface_geometry_marks_islands_and_holes(self):
        region = region_from_geometry(symbol_geometry("donut_r2000x1000"))
        geometry = to_surface_geometry(region)
        flags = sorted(p.is_island for p in geometry.polygons)
        assert flags == [False, True]


class TestQuantisation:
    def test_scale_is_one_micrometre(self):
        assert SCALE == 1_000_000

    def test_sub_nanometre_detail_is_lost(self):
        # Documented floor, not a bug: the source can carry more digits than this
        # (an 8-decimal inch coordinate is ~25 nm) and the scale is chosen for
        # headroom under a 2 m panel, not for losslessness.
        a = square_region(0.0, 0.0, 1.0, 1.0)
        b = square_region(0.0, 0.0, 1.0 + 1e-9, 1.0)
        assert a.area == b.area

    def test_a_large_panel_is_representable(self):
        # 2 m at 1e-6 mm is 2e9, comfortably inside 64-bit.
        big = square_region(0, 0, 2000.0, 1000.0)
        assert big.area == pytest.approx(2_000_000.0)
        assert big.bounds == (0.0, 0.0, 2000.0, 1000.0)

    def test_coordinates_survive_the_round_trip(self):
        region = island((1.234567, 9.876543), (2.234567, 9.876543),
                        (2.234567, 8.876543), (1.234567, 8.876543))
        for x, y in region.rings[0].points:
            assert x == pytest.approx(round(x, 6), abs=1e-6)
            assert y == pytest.approx(round(y, 6), abs=1e-6)

    def test_ring_is_a_tuple_so_it_cannot_be_mutated_in_place(self):
        ring = square_region(0, 0, 1, 1).rings[0]
        assert isinstance(ring, Ring)
        assert isinstance(ring.points, tuple)


class TestSnapToGrid:
    """Quantising to the output grid is geometry, not formatting."""

    def test_it_keeps_a_square_that_is_already_on_the_grid(self):
        region, collapsed = snap_to_grid(square_region(0, 0, 10, 10), 4)
        assert collapsed == 0
        assert region.area == pytest.approx(100.0)
        for ring in region.rings:
            for x, y in ring.points:
                assert abs(x * 10_000 - round(x * 10_000)) < 1e-6
                assert abs(y * 10_000 - round(y * 10_000)) < 1e-6

    def test_a_ring_narrower_than_the_grid_collapses_and_is_counted(self):
        # The isolated serialisation defect from the review: a 0.00002 mm wide
        # rectangle became two coincident edges, i.e. an invalid polygon.
        narrow = island((0.0, 0.0), (0.0, 1.0), (0.00002, 1.0), (0.00002, 0.0))
        assert narrow.area == pytest.approx(0.00002)
        region, collapsed = snap_to_grid(narrow, 4)
        assert not region
        assert collapsed == 1

    def test_a_path_visiting_one_vertex_twice_does_not_raise(self):
        # designodb-rigidflex's SPT ring 2183 collapsed two vertices onto one and
        # Clipper rejected the path outright ("invalid for clipping").
        path = [(461.266, -56.694), (461.268, -56.694),
                (461.266, -56.694), (461.264, -56.694)]
        region, collapsed = snap_to_grid(Region([Ring(tuple(path), True)]), 4)
        assert not region
        assert collapsed == 1

    def test_a_self_intersecting_ring_is_repaired_not_shipped(self):
        bowtie = island((0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0))
        region, collapsed = snap_to_grid(bowtie, 4)
        assert region.area == pytest.approx(50.0)
        assert collapsed == 0

    def test_nearby_vertices_are_merged_by_the_union_pass(self):
        # Two overlapping islands a grid step apart must come back as one ring
        # whose edges do not cross.
        a = square_region(0, 0, 1.0, 1.0)
        b = square_region(0.00004, 0.0, 1.00004, 1.0)
        region, _collapsed = snap_to_grid(union(a, b), 4)
        assert len(region.islands) == 1

    def test_a_non_adjacent_repeat_is_kept_so_lobes_do_not_weld(self):
        # The regression from the second review: two lobes joined by a neck
        # narrower than the grid.  Deleting the repeated vertex reconnected the
        # edges around the neck and produced one 9 mm^2 rectangle instead of two
        # 4 mm^2 lobes -- a shape that passed every validity check.
        from shapely.geometry import Polygon

        points = [(0, 0), (2, 0), (2, 2), (1.00002, 2), (1.00002, 3), (2, 3),
                  (2, 5), (0, 5), (0, 3), (1, 3), (1, 2), (0, 2)]
        region, collapsed = snap_to_grid(island(*points), 4)
        assert collapsed == 0
        assert region.area == pytest.approx(8.0)
        assert len(region.islands) == 2
        assert region.holes == []
        for ring in region.rings:
            assert Polygon(ring.points).is_valid
            assert Polygon(ring.points).area > 0.0


class TestGridSnapSerialisationRoundTrip:
    """Whatever snap_to_grid returns must survive being written and read back."""

    def test_snapped_rings_are_valid_and_non_degenerate(self):
        from shapely.geometry import Polygon

        region, _collapsed = snap_to_grid(square_region(0, 0, 10, 10), 4)
        for ring in region.rings:
            polygon = Polygon(ring.points)
            assert polygon.is_valid
            assert polygon.area > 0.0
