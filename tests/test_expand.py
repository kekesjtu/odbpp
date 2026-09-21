"""Feature expansion: symbol resolution, placement, sweeps and polarity.

The assertions are mostly areas and ring counts, because the failure modes here
are the ones that look right on screen:

* a pad that is not translated still draws -- as a dot at the origin (this
  actually shipped and was caught by rendering the smoke-test fixture, whose
  entire purpose is a row of pads at distinct positions);
* a void on a negative layer that is dropped gives a fully plated plane instead
  of one with clearance holes;
* a silkscreen layer seeded with the board outline reports the *board's* area
  instead of its own artwork's.
"""

from __future__ import annotations

import math

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.expand import (
    ExpandOptions,
    Expander,
    LayerGeometry,
    expand_layer,
    stroke_of,
)
from odbpp.features import FeatureFile, SymbolRef
from odbpp.job import LoadOptions, load_job
from odbpp.model import Orientation, Polarity
from odbpp.symbols import SymbolDefinition, parse_standard_symbol
from odbpp.units import UnitScope, UnitSystem

from conftest import fixture_path, sample_path



def load(name: str, **kwargs):
    diagnostics = Diagnostics()
    options = kwargs.pop("options", None) or LoadOptions()
    job = load_job(str(sample_path(name)), options=options, diagnostics=diagnostics)
    expander = Expander(job.symbols, diagnostics=diagnostics, **kwargs)
    return job, job.single_step, expander, diagnostics


def layer_of(step, name: str):
    layer = step.layer(name)
    assert layer is not None, f"no layer {name!r}"
    return layer


def compose(step, layer, expander, options=None) -> LayerGeometry:
    return expand_layer(layer, step, expander=expander, options=options)


# ---------------------------------------------------------------------------
# stroke descriptors
# ---------------------------------------------------------------------------

class TestStrokeOf:
    def test_a_round_symbol_is_a_round_stroke_of_half_its_diameter(self):
        stroke = stroke_of(parse_standard_symbol("r1000", UnitSystem.MM))
        assert stroke.kind == "round"
        assert stroke.size_mm == pytest.approx(0.5)

    def test_a_square_symbol_is_a_square_stroke_of_its_side(self):
        stroke = stroke_of(parse_standard_symbol("s500", UnitSystem.MM))
        assert stroke.kind == "square"
        assert stroke.size_mm == pytest.approx(0.5)

    def test_inch_sizes_use_the_size_semantic(self):
        # 3.937 mil = 0.1 mm.  Using the coordinate semantic would give 3.937
        # *inches* and produce a 100 mm wide trace instead of a 0.1 mm one.
        stroke = stroke_of(parse_standard_symbol("r3.937", UnitSystem.INCH))
        assert stroke.size_mm == pytest.approx(0.05, abs=1e-6)

    def test_a_non_round_non_square_symbol_has_no_stroke(self):
        for name in ("rect1000x500", "oval1000x500", "donut_r2000x1000"):
            assert stroke_of(parse_standard_symbol(name, UnitSystem.MM)) is None

    def test_an_unresolved_symbol_has_no_stroke(self):
        assert stroke_of(None) is None

    def test_a_user_defined_symbol_is_a_custom_stroke(self):
        definition = SymbolDefinition(name="custom", dir_name="custom", features=None)
        stroke = stroke_of(definition)
        assert stroke.kind == "custom"
        assert stroke.name == "custom"

    def test_the_key_is_quantised(self):
        a = stroke_of(parse_standard_symbol("r1000", UnitSystem.MM))
        b = stroke_of(parse_standard_symbol("r1000.0000001", UnitSystem.MM))
        assert a.key == b.key


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------

class TestPlacement:
    """spec p.34-35: rotate first, then mirror along x, then translate."""

    def test_a_pad_is_translated_to_its_own_position(self):
        # The regression this exists for: _place() computed the translated points
        # and then returned the original ring when there was no rotation, so every
        # pad in the job landed on the origin and unioned into one blob.
        region = Expander._place(
            _unit_square(), (100.0, 50.0), Orientation())
        assert region.bounds == pytest.approx((99.5, 49.5, 100.5, 50.5))

    def test_two_pads_at_different_points_stay_apart(self):
        square = _unit_square()
        a = Expander._place(square, (0.0, 0.0), Orientation())
        b = Expander._place(square, (10.0, 0.0), Orientation())
        from odbpp.clipper import union

        assert len(union(a, b).rings) == 2

    def test_translation_alone_preserves_winding(self):
        region = Expander._place(_unit_square(), (5.0, 5.0), Orientation())
        assert len(region.islands) == 1
        assert region.holes == []

    def test_rotation_is_clockwise(self):
        # A point at (1, 0) rotated 90 degrees clockwise goes to (0, -1).
        region = Expander._place(
            _spike(), (0.0, 0.0), Orientation(rotation_deg=90.0))
        xs = [x for ring in region.rings for x, _ in ring.points]
        ys = [y for ring in region.rings for _, y in ring.points]
        assert max(ys) < 1e-9
        assert min(xs) > -4.0

    def test_mirroring_negates_y(self):
        region = Expander._place(
            _spike(), (0.0, 0.0), Orientation(mirrored=True))
        ys = [y for ring in region.rings for _, y in ring.points]
        assert min(ys) <= -3.9

    def test_mirroring_restores_the_spec_winding(self):
        # A mirror negates the signed area, so an island would come out
        # counter-clockwise unless the winding is re-derived.
        from odbpp.geometry import is_clockwise

        region = Expander._place(
            _unit_square(), (0.0, 0.0), Orientation(mirrored=True))
        assert is_clockwise(list(region.islands[0].points))

    def test_rotation_then_mirroring_is_not_the_same_as_the_reverse(self):
        rotate = Expander._place(_spike(), (0.0, 0.0),
                                 Orientation(rotation_deg=90.0, mirrored=True))
        mirror_only = Expander._place(_spike(), (0.0, 0.0),
                                      Orientation(mirrored=True))
        assert rotate.bounds != mirror_only.bounds

    def test_an_identity_placement_returns_the_same_region(self):
        source = _unit_square()
        assert Expander._place(source, (0.0, 0.0), Orientation()) is source

    def test_placing_an_empty_region_is_empty(self):
        from odbpp.clipper import Region

        assert not Expander._place(Region(), (1.0, 2.0), Orientation())


def _unit_square():
    from odbpp.clipper import region_from_rings

    return region_from_rings([([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5),
                                (-0.5, 0.5)], False)])


def _spike():
    """An asymmetric triangle, so rotation and mirroring are distinguishable."""
    from odbpp.clipper import region_from_rings

    return region_from_rings([([(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)], False)])


# ---------------------------------------------------------------------------
# polarity composition
# ---------------------------------------------------------------------------

class TestPolarity:
    def test_a_positive_layer_with_a_negative_record_gets_a_hole(self):
        # fx_negative_layer's f.cu has two pads and one N-polarity clearance.
        _job, step, expander, _diag = load("fx_negative_layer")
        result = compose(step, layer_of(step, "F.CU"), expander)
        assert len(result.region.holes) >= 1
        assert result.region.area < sum(r.area for r in result.region.islands)

    def test_a_negative_layer_subtracts_its_positive_records(self):
        # GND is POLARITY=NEGATIVE: the profile is the material and the two
        # positive records are clearances.  fx_negative_layer's profile is 20x10
        # = 200 mm^2, so the result must be less than that but not empty.
        _job, step, expander, _diag = load("fx_negative_layer")
        layer = layer_of(step, "GND")
        assert layer.is_negative
        result = compose(step, layer, expander)
        assert result.used_base
        assert 0.0 < result.region.area < 200.0
        assert result.region.holes

    def test_a_negative_layer_is_not_blank(self):
        # The regression: accumulating the runs separately and then subtracting
        # them from the base made the first positive run subtract from an empty
        # accumulator, so a plane layer composed to nothing at all.
        _job, step, expander, _diag = load("fx_negative_layer")
        result = compose(step, layer_of(step, "GND"), expander)
        assert result.region.area > 1.0


class TestBaseGeometry:
    """AGENTS constraint 3 decides which layers start from a filled outline."""

    def test_a_dielectric_layer_starts_from_the_profile(self):
        _job, step, expander, _diag = load("fx_negative_layer")
        result = compose(step, layer_of(step, "DIELECTRIC_1"), expander)
        assert result.used_base
        # The layer's own profile is the square (1,1)-(9,9), i.e. 64 mm^2, and it
        # takes priority over the step's (0,0)-(20,10), which is 200 mm^2.  The
        # numbers differ enough that confusing the two cannot pass by accident.
        assert result.region.area == pytest.approx(64.0, rel=0.01)

    def test_a_pattern_layer_does_not_start_from_the_profile(self):
        # An earlier version seeded every non-copper layer with the board
        # outline.  Silkscreen, soldermask and every DOCUMENT layer then reported
        # the board's area instead of their own artwork.
        _job, step, expander, _diag = load("fx_negative_layer")
        for name in ("F.CU", "COMP_+_TOP", "COMP_+_BOT", "DRILL_1_2"):
            result = compose(step, layer_of(step, name), expander)
            assert not result.used_base, name

    def test_an_empty_copper_layer_stays_empty(self):
        _job, step, expander, _diag = load("fx_negative_layer")
        result = compose(step, layer_of(step, "COMP_+_TOP"), expander)
        assert result.region.area == 0.0


# ---------------------------------------------------------------------------
# AGENTS constraint 3: drills projected through a dielectric
# ---------------------------------------------------------------------------

def _square_profile(x0: float, y0: float, x1: float, y1: float):
    from odbpp.model import ContourPart, Polygon, SurfaceGeometry

    corners = [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return SurfaceGeometry(polygons=[Polygon(
        is_island=True, start=(x0, y0),
        parts=[ContourPart(kind="segment", end=point) for point in corners],
    )])


def _drill_step(*, include: tuple[str, ...] = ("D_1_2",)):
    """SIGNAL_1 / DIELECTRIC_A / SIGNAL_2 / INTERSTITIAL / SIGNAL_3.

    ``D_1_2`` crosses DIELECTRIC_A (planes 0..1); ``D_2_3`` starts at that
    dielectric's *bottom* plane and must not touch it; ``D_1_3`` crosses both.
    """
    from odbpp.features import PadFeature
    from odbpp.model import Layer, LayerRef, LayerType, Step, StepHeader

    units = UnitScope.metric()

    def new_layer(name, layer_type, row, **kw) -> Layer:
        return Layer(ref=LayerRef(name=name, row=row, type=layer_type, **kw),
                     dir_name=name)

    def sheet(name, row, profile) -> Layer:
        layer = new_layer(name, LayerType.DIELECTRIC, row)
        layer.profile = profile
        layer.features = FeatureFile(path=name, units=units, records=[])
        return layer

    def drill(name, row, start, end) -> Layer:
        record = PadFeature(line_no=1, x_mm=5.0, y_mm=5.0,
                            symbol=SymbolRef(1, "r2000"))
        layer = new_layer(name, LayerType.DRILL, row,
                          start_name=start, end_name=end)
        layer.features = FeatureFile(path=name, units=units, records=[record])
        return layer

    outline = _square_profile(0.0, 0.0, 10.0, 10.0)
    layers = [
        new_layer("SIGNAL_1", LayerType.SIGNAL, 1),
        sheet("DIELECTRIC_A", 2, outline),
        new_layer("SIGNAL_2", LayerType.SIGNAL, 3),
        sheet("INTERSTITIAL", 4, outline),
        new_layer("SIGNAL_3", LayerType.SIGNAL, 5),
    ]
    spans = {"D_1_2": ("SIGNAL_1", "SIGNAL_2"),
             "D_2_3": ("SIGNAL_2", "SIGNAL_3"),
             "D_1_3": ("SIGNAL_1", "SIGNAL_3")}
    for row, name in enumerate(include, start=6):
        layers.append(drill(name, row, *spans[name]))
    return Step(name="S", header=StepHeader(units=units), layers=layers,
                profile=outline)


class TestDrillProjection:
    def test_a_crossing_drill_is_removed_from_the_dielectric(self):
        step = _drill_step()
        expander = Expander(None)
        result = expand_layer(step.layer("DIELECTRIC_A"), step, expander=expander)
        assert result.used_base
        # 10x10 outline minus the r2000 pad (2 mm diameter, r = 1 mm).  The pad
        # is tessellated, so the hole is a hair under pi mm^2 -- hence rel=1e-2.
        assert result.region.area == pytest.approx(100.0 - math.pi, rel=1e-2)
        assert result.drills_subtracted == ["D_1_2"]
        assert result.drill_cut_mm2 == pytest.approx(math.pi, rel=1e-2)
        assert result.region.holes

    def test_a_drill_starting_below_does_not_touch_it(self):
        # D_2_3 spans planes (1, 2); the dielectric spans (0, 1).  Half-open
        # intervals are what keep the blind drill out of it.
        step = _drill_step(include=("D_2_3",))
        result = expand_layer(step.layer("DIELECTRIC_A"), step,
                              expander=Expander(None))
        assert result.drills_subtracted == []
        assert result.region.area == pytest.approx(100.0)

    def test_a_through_drill_crosses_both_dielectrics(self):
        step = _drill_step(include=("D_1_3",))
        expander = Expander(None)
        upper = expand_layer(step.layer("DIELECTRIC_A"), step, expander=expander)
        lower = expand_layer(step.layer("INTERSTITIAL"), step, expander=expander)
        assert upper.drills_subtracted == ["D_1_3"]
        assert lower.drills_subtracted == ["D_1_3"]
        assert upper.region.area == pytest.approx(100.0 - math.pi, rel=1e-2)

    def test_an_empty_features_file_still_gets_its_holes(self):
        # The regression: the early return for "no records" used to hand back the
        # bare board outline, so a real dielectric (whose features file is often
        # empty) filled every drill.
        step = _drill_step()
        dielectric = step.layer("DIELECTRIC_A")
        assert dielectric.features.records == []
        result = expand_layer(dielectric, step, expander=Expander(None))
        assert result.drills_subtracted == ["D_1_2"]
        assert result.region.area < 100.0

    def test_an_unresolved_span_subtracts_nothing_and_warns(self):
        diagnostics = Diagnostics()
        step = _drill_step()
        step.layer("D_1_2").ref.end_name = ""
        expander = Expander(None, diagnostics=diagnostics)
        result = expand_layer(step.layer("DIELECTRIC_A"), step, expander=expander)
        assert result.region.area == pytest.approx(100.0)
        assert result.drills_subtracted == []
        assert "stackup.drill_span_unknown" in diagnostics.codes()


# ---------------------------------------------------------------------------
# symbol resolution
# ---------------------------------------------------------------------------

class TestSymbolResolution:
    def test_standard_symbols_expand_without_a_library(self):
        # Standard names are arithmetic, so a job whose features skip the loader
        # still renders them; only symbols/ lookups need the archive.
        _job, step, expander, _diag = load("minimal-test-odb")
        expander.library = None
        result = compose(step, layer_of(step, "top"), expander)
        assert result.region.area > 0.0

    def test_user_defined_symbols_need_the_library(self):
        # The other half of the same rule: a symbols/ name is not derivable from
        # the name, so without the library there is nothing to expand.
        _job, _step, expander, _diag = load("fx_nested_symbol")
        ref = SymbolRef(0, "outer")
        assert expander._resolve(ref, UnitSystem.MM) is not None
        expander.library = None
        assert expander._resolve(ref, UnitSystem.MM) is None

    def test_a_missing_symbol_is_reported_once_and_contributes_nothing(self):
        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)

        # A name that is neither standard grammar nor present on disk.  r1000
        # would resolve -- standard names are arithmetic.
        ref = SymbolRef(0, "MYSTERY_PAD")
        for _ in range(5):
            assert expander._resolve(ref, UnitSystem.MM) is None
            expander._missing_symbol_warning(ref)
        codes = [d.code for d in diagnostics]
        assert codes.count("geometry.symbol_undefined") == 1

    def test_a_user_defined_symbol_expands_through_its_feature_file(self):
        _job, step, expander, _diag = load("fx_nested_symbol")
        result = compose(step, layer_of(step, "F.CU"), expander)
        assert result.region.area > 0.0
        assert result.records > 0

    def test_the_job_carries_a_preloaded_library(self):
        # Detached on purpose: the archive is closed by the time loading returns,
        # so the definitions that will be rendered must already be resident.
        job, _step, _expander, _diag = load("fx_nested_symbol")
        assert job.symbols is not None
        assert job.symbols.has("outer")

    def test_symbol_geometry_is_cached(self):
        _job, _step, expander, _diag = load("fx_nested_symbol")
        target = parse_standard_symbol("rect3000x1500", UnitSystem.MM)
        first = expander.symbol_geometry(target)
        second = expander.symbol_geometry(target)
        assert first is second

    def test_an_unsupported_family_is_reported_rather_than_faked(self):
        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)
        target = parse_standard_symbol("moire100x50x4x10x20x0", UnitSystem.MM)
        assert expander.symbol_geometry(target) is None
        assert "geometry.symbol_unsupported" in diagnostics.codes()


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------

class TestSweeps:
    def test_a_round_line_matches_the_stadium_formula(self):
        # minimal-test-odb draws its traces with r500 (0.5 mm diameter) and
        # r1000 (1 mm).  fs a single L record and check the band width.
        _job, step, expander, _diag = load("minimal-test-odb")
        features = step.layer("top").features
        lines = [r for r in features.records if r.kind == "line"]
        assert lines
        record = lines[0]

        from odbpp.clipper import stroke_paths

        radius = record.symbol and stroke_of(
            expander._resolve(record.symbol, features.units.system)).size_mm
        band = stroke_paths([[record.start_mm, record.end_mm]], radius)
        x0, y0, x1, y1 = band.bounds
        assert (y1 - y0) == pytest.approx(2 * radius, abs=1e-6)

    def test_an_arc_sweeps_along_its_curve(self):
        _job, step, expander, _diag = load("minimal-test-odb")
        features = step.layer("top").features
        arcs = [r for r in features.records if r.kind == "arc"]
        assert arcs
        region = expander._path_points(arcs[0])
        assert len(region) > 2

    def test_a_full_circle_arc_produces_a_closed_centreline(self):
        # start == end is a 360 degree turn (spec p.40).  The old full-circle
        # fast path returned the two coincident endpoints, which deduplicated to
        # a one-point path and vanished without a diagnostic.
        from odbpp.features import ArcFeature
        from odbpp.units import UnitScope

        expander = Expander(None)
        arc = ArcFeature(line_no=1, start_mm=(5.0, 0.0), end_mm=(5.0, 0.0),
                         center_mm=(0.0, 0.0), symbol=SymbolRef(0, "r1000"))
        path = expander._path_points(arc)
        assert len(path) > 8
        assert path[0] == path[-1]

        # The swept band is the annulus 4.5..5.5 mm: pi*(5.5^2 - 4.5^2) = 10*pi.
        features = FeatureFile(path="review/full-circle", units=UnitScope.metric(),
                               records=[arc])
        region = expander.run_region(features.records, features, depth=0)
        assert region.area == pytest.approx(10 * math.pi, rel=1e-3)
        assert not [d for d in expander.diagnostics
                    if d.code == "geometry.arc_degenerate"]

    def test_a_degenerate_arc_has_no_centreline(self):
        # start == end means a 360 degree turn (spec p.40), which is a zero-length
        # centreline: sweeping it would draw nothing, so it is expanded as a ring
        # instead.  This test pins the *degenerate* case where the centre
        # coincides, which has no radius at all.
        from odbpp.features import ArcFeature

        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)
        arc = ArcFeature(line_no=1, start_mm=(5.0, 5.0), end_mm=(5.0, 5.0),
                         center_mm=(5.0, 5.0), clockwise=True)
        assert expander._path_points(arc) == []
        assert "geometry.arc_degenerate" in diagnostics.codes()

    def test_an_asymmetric_symbol_on_a_line_is_reported(self):
        from odbpp.features import LineFeature

        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)
        features = _feature_file_with([SymbolRef(0, "rect1000x500")])
        record = LineFeature(line_no=1, start_mm=(0.0, 0.0), end_mm=(5.0, 0.0),
                             symbol=SymbolRef(0, "rect1000x500"))
        region = expander.record_region(record, features)
        assert region is not None and region.area > 0.0
        assert "geometry.sweep_asymmetric" in diagnostics.codes()


def _feature_file_with(symbols):
    file = FeatureFile(path="<test>", units=UnitScope.metric())
    file.symbols = list(symbols)
    return file


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------

class TestBatching:
    def test_records_of_one_polarity_become_one_run(self):
        _job, step, expander, _diag = load("minimal-test-odb")
        result = compose(step, layer_of(step, "top"), expander)
        assert result.polarity_runs == 1

    def test_a_polarity_change_starts_a_new_run(self):
        from odbpp.expand import _polarity_runs
        from odbpp.features import PadFeature

        records = [
            PadFeature(line_no=1, polarity=Polarity.POSITIVE),
            PadFeature(line_no=2, polarity=Polarity.POSITIVE),
            PadFeature(line_no=3, polarity=Polarity.NEGATIVE),
            PadFeature(line_no=4, polarity=Polarity.POSITIVE),
        ]
        runs = list(_polarity_runs(records))
        assert [len(run) for _positive, run in runs] == [2, 1, 1]
        assert [positive for positive, _run in runs] == [True, False, True]

    def test_batching_many_lines_of_one_width_matches_them_separately(self):
        # The whole point of batching is that it is unobservable.  Two traces of
        # the same width swept in one call must equal them swept one at a time.
        from odbpp.clipper import stroke_paths, union

        paths = [[(0, 0), (10, 0)], [(0, 5), (10, 5)], [(20, 0), (20, 10)]]
        batched = stroke_paths(paths, 0.3)
        separate = union(*[stroke_paths([p], 0.3) for p in paths])
        assert batched.area == pytest.approx(separate.area)
        assert batched.bounds == pytest.approx(separate.bounds)


# ---------------------------------------------------------------------------
# zero-width artwork
# ---------------------------------------------------------------------------

class TestZeroWidthStrokes:
    def test_zero_width_artwork_vanishes_by_default(self):
        _job, step, expander, _diag = load("minimal-test-odb")
        features = step.layer("top").features
        from odbpp.features import LineFeature, SymbolRef

        record = LineFeature(line_no=1, start_mm=(0.0, 0.0), end_mm=(10.0, 0.0),
                             symbol=SymbolRef(0, "r0"))
        assert expander.record_region(record, features) is None or \
            not expander.record_region(record, features)

    def test_the_hairline_option_makes_it_visible_and_says_so(self):
        from odbpp.features import LineFeature

        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics,
                            options=ExpandOptions(minimum_stroke_mm=0.1))
        features = _feature_file_with([SymbolRef(0, "r0")])
        record = LineFeature(line_no=1, start_mm=(0.0, 0.0), end_mm=(10.0, 0.0),
                             symbol=SymbolRef(0, "r0"))
        region = expander.record_region(record, features)
        assert region is not None
        x0, y0, x1, y1 = region.bounds
        assert (y1 - y0) == pytest.approx(0.1, abs=1e-6)
        assert "geometry.zero_width_stroke" in diagnostics.codes()


# ---------------------------------------------------------------------------
# resize
# ---------------------------------------------------------------------------

class TestResize:
    def test_a_resize_factor_grows_the_pad_and_is_flagged_as_unverified(self):
        # spec p.170's prose and its own example disagree by 1000x, and no case in
        # cases/ exercises the field, so the choice has to be visible.
        from odbpp.features import PadFeature

        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)
        features = _feature_file_with([SymbolRef(0, "s1000")])

        plain = PadFeature(line_no=1, x_mm=0.0, y_mm=0.0,
                           symbol=SymbolRef(0, "s1000"))
        resized = PadFeature(line_no=2, x_mm=0.0, y_mm=0.0,
                             symbol=SymbolRef(0, "s1000"),
                             resize_raw=200.0, resize_unit=UnitSystem.MM)
        assert expander.record_region(resized, features).area > \
            expander.record_region(plain, features).area
        assert "geometry.resize_unverified" in diagnostics.codes()

    def test_the_warning_is_emitted_once_per_expander(self):
        from odbpp.features import PadFeature

        diagnostics = Diagnostics()
        expander = Expander(None, diagnostics=diagnostics)
        features = _feature_file_with([SymbolRef(0, "s1000")])

        for index in range(4):
            expander.record_region(
                PadFeature(line_no=index, symbol=SymbolRef(0, "s1000"),
                           resize_raw=100.0, resize_unit=UnitSystem.MM),
                features)
        codes = [d.code for d in diagnostics]
        assert codes.count("geometry.resize_unverified") == 1


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------

class TestRealCases:
    """Skipped when the samples are not present; see cases/README.md."""

    def test_minimal_board_composes_both_copper_layers(self):
        from conftest import case_path

        diagnostics = Diagnostics()
        job = load_job(str(case_path("minimal-test-odb")), diagnostics=diagnostics)
        step = job.single_step
        expander = Expander(job.symbols, diagnostics=diagnostics)
        results = {l.name: compose(step, l, expander) for l in step.layers}
        assert results["top"].region.area > 0.0
        assert results["bottom"].region.area > 0.0
        # The pads distinguish the two layers: 'top' carries the symbol test row.
        assert len(results["top"].region.rings) > len(results["bottom"].region.rings)
        assert not diagnostics.errors

    def test_a_layer_with_a_broken_profile_is_reported_not_substituted(self):
        diagnostics = Diagnostics()
        job = load_job(str(fixture_path("fx_bad_layer_profile")),
                       diagnostics=diagnostics)
        step = job.single_step
        expander = Expander(job.symbols, diagnostics=diagnostics)
        from odbpp.errors import LayerProfileError

        with pytest.raises(LayerProfileError):
            compose(step, layer_of(step, "DIELECTRIC_1"), expander)
