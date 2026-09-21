"""Geometry tests: arc flattening, winding, and native symbol expansion.

Two properties are load-bearing for everything downstream:

* every generated island ring is **clockwise** and every hole ring is
  **counter-clockwise** (spec p.39), so the eventual boolean stage can trust the
  orientation instead of re-deriving it;
* every shape spans exactly the width and height its name asked for -- an
  ellipse-inscribed hexagon, for instance, falls short of its own bounding box.
"""

from __future__ import annotations

import math

import pytest

from odbpp.geometry import (
    DEFAULT_SAGITTA_MM,
    SUPPORTED_SYMBOL_PREFIXES,
    TessellationOptions,
    arc_points,
    bounding_box,
    contour_points,
    ensure_winding,
    geometry_points,
    is_clockwise,
    polygon_points,
    rotate_points,
    signed_area,
    standard_symbol_geometry,
    transform_points,
)
from odbpp.model import ContourPart, Polygon, SurfaceGeometry
from odbpp.symbols import parse_standard_symbol
from odbpp.units import UnitSystem


def expand(name: str, unit: UnitSystem = UnitSystem.MM,
           options: TessellationOptions | None = None):
    symbol = parse_standard_symbol(name, unit)
    assert symbol is not None, name
    return standard_symbol_geometry(symbol, options)


def rings(name: str, **kwargs):
    return [polygon_points(p, kwargs.get("options"))
            for p in expand(name, kwargs.get("unit", UnitSystem.MM),
                            kwargs.get("options")).polygons]


class TestArcPoints:
    def test_quarter_turn_counter_clockwise(self):
        points = arc_points((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False)
        assert points[-1] == (0.0, 1.0)
        assert all(x >= -1e-9 and y >= -1e-9 for x, y in points)
        # Every intermediate point is on the circle.
        for x, y in points[:-1]:
            assert math.hypot(x, y) == pytest.approx(1.0, abs=1e-9)

    def test_quarter_turn_clockwise_goes_the_other_way(self):
        points = arc_points((1.0, 0.0), (0.0, -1.0), (0.0, 0.0), clockwise=True)
        assert points[-1] == (0.0, -1.0)
        assert all(y <= 1e-9 for _, y in points)

    def test_start_is_excluded_so_a_contour_does_not_repeat_itself(self):
        points = arc_points((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False)
        assert (1.0, 0.0) not in points

    def test_coincident_endpoints_mean_a_full_circle(self):
        # Spec p.40: "When the start-point and end-point of an arc coincide, it is
        # considered a 360 degree arc."
        points = arc_points((1.0, 0.0), (1.0, 0.0), (0.0, 0.0), clockwise=False)
        assert len(points) > 8
        assert points[-1] == (1.0, 0.0)
        # A full turn covers all four quadrants.
        assert any(y > 0.5 for _, y in points)
        assert any(y < -0.5 for _, y in points)
        assert any(x < -0.5 for x, _ in points)

    def test_the_endpoint_is_returned_verbatim(self):
        # Keeps the recorded polygon closure exact even when the authored radii
        # differ by a rounding amount.
        points = arc_points((1.0, 0.0), (0.0, 1.002), (0.0, 0.0), clockwise=False)
        assert points[-1] == (0.0, 1.002)

    def test_a_tighter_tolerance_adds_segments(self):
        coarse = arc_points((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False,
                            options=TessellationOptions(sagitta_mm=0.1))
        fine = arc_points((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False,
                          options=TessellationOptions(sagitta_mm=0.0001))
        assert len(fine) > len(coarse)

    def test_the_tessellation_respects_its_own_sagitta(self):
        options = TessellationOptions(sagitta_mm=0.01)
        points = arc_points((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False,
                            options=options)
        chain = [(1.0, 0.0)] + points
        for (x1, y1), (x2, y2) in zip(chain, chain[1:]):
            # distance from the origin to the chord midpoint never drops below
            # radius - sagitta
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            assert math.hypot(mx, my) >= 1.0 - options.sagitta_mm - 1e-12

    def test_a_degenerate_arc_falls_back_to_its_chord(self):
        assert arc_points((1.0, 1.0), (2.0, 2.0), (1.0, 1.0), clockwise=False) == [(2.0, 2.0)]

    def test_options_reject_nonsense(self):
        with pytest.raises(ValueError):
            TessellationOptions(sagitta_mm=0.0)
        with pytest.raises(ValueError):
            TessellationOptions(max_segments=0)


class TestContourPoints:
    def test_segments_only(self):
        polygon = Polygon(is_island=True, start=(0.0, 0.0), parts=[
            ContourPart("segment", (1.0, 0.0)),
            ContourPart("segment", (1.0, 1.0)),
            ContourPart("segment", (0.0, 0.0)),
        ])
        assert polygon_points(polygon) == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]

    def test_arcs_are_inserted_between_their_endpoints(self):
        polygon = Polygon(is_island=True, start=(1.0, 0.0), parts=[
            ContourPart("arc", (0.0, 1.0), center=(0.0, 0.0), clockwise=False),
        ])
        points = polygon_points(polygon)
        assert points[0] == (1.0, 0.0)
        assert points[-1] == (0.0, 1.0)
        assert len(points) > 2

    def test_an_arc_without_a_centre_is_an_error(self):
        from odbpp.errors import UnsupportedFeatureError

        with pytest.raises(UnsupportedFeatureError):
            contour_points((0.0, 0.0), [ContourPart("arc", (1.0, 1.0))])

    def test_geometry_points_keeps_islands_and_holes_apart(self):
        geometry = SurfaceGeometry(polygons=[
            Polygon(is_island=True, start=(0.0, 0.0),
                    parts=[ContourPart("segment", p) for p in
                           ((10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0))]),
            Polygon(is_island=False, start=(4.0, 4.0),
                    parts=[ContourPart("segment", p) for p in
                           ((6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0))]),
        ])
        flattened = geometry_points(geometry)
        assert [p.is_island for p in flattened] == [True, False]
        assert len(flattened[0].parts) == 4


class TestWinding:
    def test_signed_area_sign_follows_the_winding(self):
        ccw = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert signed_area(ccw) > 0
        assert not is_clockwise(ccw)
        assert is_clockwise(list(reversed(ccw)))

    def test_ensure_winding_reverses_when_needed(self):
        ccw = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        assert is_clockwise(ensure_winding(list(ccw), True))
        assert not is_clockwise(ensure_winding(list(ccw), False))

    def test_ensure_winding_is_a_no_op_when_already_correct(self):
        cw = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        assert ensure_winding(cw, True) == cw

    def test_degenerate_rings_are_left_alone(self):
        assert ensure_winding([(0.0, 0.0)], True) == [(0.0, 0.0)]


class TestBoundingBox:
    def test_reports_min_then_max(self):
        assert bounding_box([(1.0, 5.0), (-2.0, 3.0), (4.0, -1.0)]) == (-2.0, -1.0, 4.0, 5.0)

    def test_empty_input_is_an_error(self):
        with pytest.raises(ValueError):
            bounding_box([])


class TestTransforms:
    def test_rotation_is_clockwise(self):
        # Spec p.34: ODB++ rotation is clockwise, so (1, 0) at 90 deg -> (0, -1).
        assert rotate_points([(1.0, 0.0)], 90.0)[0] == pytest.approx((0.0, -1.0))

    def test_mirror_is_along_the_x_axis_only(self):
        assert transform_points([(1.0, 2.0)], mirrored=True) == [(1.0, -2.0)]

    def test_rotation_happens_before_mirroring(self):
        # Spec p.34: "rotated first, and then mirrored along the x-axis".
        # If the order were reversed, (1, 0) would land at (-1, 0) instead.
        result = transform_points([(1.0, 0.0)], angle=90.0, mirrored=True)
        assert result[0] == pytest.approx((0.0, 1.0))


class TestStandardSymbolShapes:
    def test_every_supported_prefix_expands(self):
        probes = {
            "r": "r1000", "s": "s1000", "rect": "rect1000x500",
            "oval": "oval1000x500", "oval_h": "oval_h1000x500",
            "di": "di1000x500", "oct": "oct1000x1000x100",
            "tri": "tri1000x800", "hex_l": "hex_l1000x800",
            "hex_s": "hex_s1000x800", "donut_r": "donut_r2000x1000",
            "donut_s": "donut_s2000x1000", "donut_rc": "donut_rc1000x500x100",
            "donut_o": "donut_o1000x500x100",
        }
        assert set(probes) == SUPPORTED_SYMBOL_PREFIXES
        for prefix, name in probes.items():
            geometry = expand(name)
            assert geometry.polygons, prefix

    @pytest.mark.parametrize(
        "name,width,height",
        [
            ("s1000", 1.0, 1.0),
            ("rect1000x500", 1.0, 0.5),
            ("rect1000x500xr100", 1.0, 0.5),
            ("rect1000x500xc100", 1.0, 0.5),
            ("oval1000x500", 1.0, 0.5),
            ("oval500x1000", 0.5, 1.0),
            ("di1000x500", 1.0, 0.5),
            ("oct1000x1000x100", 1.0, 1.0),
            ("oct1000x1000", 1.0, 1.0),
            ("tri1000x800", 1.0, 0.8),
            ("hex_l1000x800", 1.0, 0.8),
            ("hex_s1000x800", 1.0, 0.8),
        ],
    )
    def test_shapes_span_exactly_their_named_box(self, name, width, height):
        ring = rings(name)[0]
        min_x, min_y, max_x, max_y = bounding_box(ring)
        assert max_x - min_x == pytest.approx(width, abs=1e-9)
        assert max_y - min_y == pytest.approx(height, abs=1e-9)
        # Centred on the origin, which is what a pad placement expects.
        assert (min_x + max_x) / 2 == pytest.approx(0.0, abs=1e-9)
        assert (min_y + max_y) / 2 == pytest.approx(0.0, abs=1e-9)

    def test_round_spans_its_diameter_and_stays_inside_it(self):
        ring = rings("r1000")[0]
        min_x, min_y, max_x, max_y = bounding_box(ring)
        assert max_x - min_x <= 1.0 + 1e-12
        assert max_x - min_x > 1.0 - 2 * DEFAULT_SAGITTA_MM
        # Inscribed, never circumscribed: no vertex leaves the true circle.
        for x, y in ring:
            assert math.hypot(x, y) <= 0.5 + 1e-12

    def test_the_regular_octagon_has_the_regular_octagon_area(self):
        ring = rings("oct1000x1000")[0]
        side = 1.0 / (1.0 + math.sqrt(2.0))
        assert -signed_area(ring) == pytest.approx(2 * (1 + math.sqrt(2)) * side ** 2,
                                                   rel=1e-9)

    def test_a_rounded_rectangle_touches_all_four_sides(self):
        ring = rings("rect1000x500xr100")[0]
        min_x, min_y, max_x, max_y = bounding_box(ring)
        assert (min_x, min_y, max_x, max_y) == pytest.approx((-0.5, -0.25, 0.5, 0.25))

    def test_a_chamfered_rectangle_loses_exactly_four_triangles(self):
        ring = rings("rect1000x500xc100")[0]
        assert -signed_area(ring) == pytest.approx(1.0 * 0.5 - 4 * 0.5 * 0.1 * 0.1)

    def test_only_the_named_corners_are_cut(self):
        # Corner 3 is the bottom-left (spec p.203 numbers them counter-clockwise
        # from the top-right).
        ring = rings("rect1000x500xc100x3")[0]
        assert -signed_area(ring) == pytest.approx(0.5 - 0.5 * 0.1 * 0.1)

    def test_a_corner_mask_outside_one_to_four_is_refused(self):
        from odbpp.errors import UnsupportedFeatureError

        # "9" is not a corner; accepting it would silently round nothing.
        symbol = parse_standard_symbol("rect1000x500xr100x9", UnitSystem.MM)
        if symbol is None:
            pytest.skip("grammar rejected the mask before geometry saw it")
        with pytest.raises(UnsupportedFeatureError):
            standard_symbol_geometry(symbol)

    def test_a_half_oval_keeps_only_the_upper_half(self):
        ring = rings("oval_h1000x500")[0]
        min_x, min_y, max_x, max_y = bounding_box(ring)
        assert min_y == pytest.approx(0.0)
        assert max_y == pytest.approx(0.25)
        assert (-0.5, 0.5) == pytest.approx((min_x, max_x))
        # Half the area of the full obround.
        assert -signed_area(ring) == pytest.approx(
            -signed_area(rings("oval1000x500")[0]) / 2, rel=0.02)

    def test_donuts_produce_an_island_and_a_hole(self):
        geometry = expand("donut_r2000x1000")
        assert len(geometry.polygons) == 2
        assert geometry.polygons[0].is_island
        assert not geometry.polygons[1].is_island

    def test_the_hole_is_smaller_than_the_island(self):
        geometry = expand("donut_r2000x1000")
        island, hole = (polygon_points(p) for p in geometry.polygons)
        assert (-signed_area(hole)) < (-signed_area(island))
        assert bounding_box(hole)[2] < bounding_box(island)[2]

    def test_the_annular_ring_has_the_expected_width(self):
        geometry = expand("donut_r2000x1000")
        island, hole = (polygon_points(p) for p in geometry.polygons)
        # Outer radius 1.0, inner radius 0.5 -> ring width 0.5.
        assert bounding_box(island)[2] == pytest.approx(1.0, abs=1e-9)
        assert bounding_box(hole)[2] == pytest.approx(0.5, abs=1e-9)

    def test_imperial_parameters_convert_through_the_size_semantic(self):
        # cases/designodb-rigidflex is INCH: donut_r78.74x27.559 is 2.000/0.700 mm.
        geometry = expand("donut_r78.74x27.559", UnitSystem.INCH)
        island, hole = (polygon_points(p) for p in geometry.polygons)
        assert bounding_box(island)[2] == pytest.approx(1.0, abs=1e-5)
        assert bounding_box(hole)[2] == pytest.approx(0.35, abs=1e-5)

    def test_zero_sized_symbols_produce_nothing_rather_than_a_degenerate_ring(self):
        assert expand("rect0x500").polygons == []
        assert expand("s0").polygons == []


class TestGeneratedRingOrientation:
    """Spec p.39: islands clockwise, holes counter-clockwise."""

    @pytest.mark.parametrize(
        "name",
        ["r1000", "s1000", "rect1000x500", "rect1000x500xr100", "rect1000x500xc100",
         "oval1000x500", "oval500x1000", "di1000x500", "oct1000x1000x100",
         "tri1000x800", "hex_l1000x800", "hex_s1000x800", "donut_r2000x1000",
         "donut_s2000x1000", "donut_rc1000x500x100", "donut_o1000x500x100"],
    )
    def test_islands_are_clockwise_and_holes_are_counter_clockwise(self, name):
        for polygon in expand(name).polygons:
            ring = polygon_points(polygon)
            if polygon.is_island:
                assert is_clockwise(ring), f"{name}: island should be clockwise"
            else:
                assert not is_clockwise(ring), f"{name}: hole should be counter-clockwise"

    def test_expansion_is_deterministic(self):
        assert rings("donut_rc1000x500x100") == rings("donut_rc1000x500x100")


class TestTessellationIsConfigurable:
    def test_a_coarse_tolerance_yields_a_smaller_polygon(self):
        coarse = expand("r10000", options=TessellationOptions(sagitta_mm=1.0))
        fine = expand("r10000", options=TessellationOptions(sagitta_mm=0.001))
        coarse_ring = polygon_points(coarse.polygons[0])
        fine_ring = polygon_points(fine.polygons[0])
        assert len(coarse_ring) < len(fine_ring)
        # Both remain inscribed in the same circle.
        assert -signed_area(coarse_ring) < -signed_area(fine_ring) <= math.pi * 25.0
