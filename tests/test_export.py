"""The intermediate JSON: units, positivity, streaming and failure handling.

The document is the contract between the parser and any consumer, so most of
these tests are about properties a consumer may rely on rather than about
specific numbers: millimetres, explicit island/hole flags, no arcs, stable schema.
"""

from __future__ import annotations

import json

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.errors import LayerProfileError
from odbpp.export import (
    SCHEMA,
    PreviewOptions,
    build_step_preview,
    iter_step_previews,
    layer_summary_lines,
    step_to_json,
)
from odbpp.job import load_job

from conftest import fixture_path, sample_path


def preview_of(name: str, **kwargs):
    diagnostics = Diagnostics()
    job = load_job(str(sample_path(name)), diagnostics=diagnostics)
    options = kwargs.pop("options", None)
    preview = build_step_preview(job, options=options, diagnostics=diagnostics)
    return job, preview, diagnostics


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

class TestDocument:
    def test_schema_and_units_are_declared(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        document = step_to_json(job, preview)
        assert document["schema"] == SCHEMA
        assert document["units"] == "mm"

    def test_input_units_are_recorded_separately_from_output_units(self):
        # AGENTS constraint 4: the output is always mm, so which system the input
        # used is a provenance fact rather than a property of the numbers.
        job, preview, _diag = preview_of("minimal-test-odb")
        document = step_to_json(job, preview)
        assert document["source"]["input_units"] == "MM"
        assert document["units"] == "mm"

    def test_the_coordinate_convention_is_stated(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        system = step_to_json(job, preview)["coordinate_system"]
        assert system["y_axis"] == "up"
        assert "clockwise" in system["winding"]
        assert system["resolution_mm"] == pytest.approx(1e-4)

    def test_it_round_trips_through_json(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        text = json.dumps(step_to_json(job, preview))
        assert json.loads(text)["steps"][0]["name"]

    def test_diagnostics_can_be_omitted(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        assert "diagnostics" in step_to_json(job, preview)
        assert "diagnostics" not in step_to_json(job, preview,
                                                 include_diagnostics=False)


# ---------------------------------------------------------------------------
# layer content
# ---------------------------------------------------------------------------

class TestLayerJson:
    def test_every_ring_carries_an_explicit_island_flag(self):
        # Nesting is stated, not left for the consumer to infer from winding --
        # even though the winding is also correct.
        _job, preview, _diag = preview_of("minimal-test-odb")
        document = step_to_json(_job, preview)
        polygons = [p for layer in document["steps"][0]["layers"]
                    for p in layer["polygons"]]
        assert polygons
        assert all(isinstance(p["island"], bool) for p in polygons)

    def test_rings_are_closed_polygons_with_at_least_three_points(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        for layer in step_to_json(_job, preview)["steps"][0]["layers"]:
            for polygon in layer["polygons"]:
                points = polygon["points"]
                assert len(points) >= 3
                assert points[0] != points[-1], "closure is implied, not repeated"
                assert all(len(pt) == 2 for pt in points)

    def test_no_polygon_carries_arc_information(self):
        # Booleans cannot carry arcs, so by this point everything is straight
        # segments.  A consumer must not expect otherwise.
        _job, preview, _diag = preview_of("minimal-test-odb")
        document = step_to_json(_job, preview)
        for layer in document["steps"][0]["layers"]:
            for polygon in layer["polygons"]:
                assert set(polygon) == {"island", "points"}

    def test_areas_and_bounds_are_present_and_consistent(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        for layer in step_to_json(_job, preview)["steps"][0]["layers"]:
            assert layer["area_mm2"] >= 0.0
            if layer["polygons"]:
                x0, y0, x1, y1 = layer["bounds_mm"]
                assert x1 >= x0 and y1 >= y0
            else:
                assert layer["bounds_mm"] is None

    def test_ring_count_matches_the_polygon_array(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        for layer_preview, layer in zip(preview.layers,
                                       step_to_json(job, preview)["steps"][0]["layers"]):
            assert len(layer_preview.rings) == len(layer["polygons"])

    def test_coordinates_are_rounded_as_asked(self):
        job, preview, _diag = preview_of("minimal-test-odb")
        for digits in (1, 2, 4):
            document = step_to_json(job, preview, round_digits=digits)
            for layer in document["steps"][0]["layers"]:
                for polygon in layer["polygons"]:
                    for x, y in polygon["points"]:
                        assert x == pytest.approx(round(x, digits), abs=1e-12)
                        assert y == pytest.approx(round(y, digits), abs=1e-12)


    def test_layer_summary_carries_the_ref_fields(self):
        _job, preview, _diag = preview_of("fx_negative_layer")
        data = {layer["name"]: layer for layer in
                step_to_json(_job, preview)["steps"][0]["layers"]}
        gnd = data["GND"]
        assert gnd["polarity"] == "N"
        assert gnd["context"] == "BOARD"
        assert gnd["type"] == "POWER_GROUND"
        assert gnd["base_from_profile"] is True

    def test_form_travels_with_its_provenance(self):
        # is_flex is always a real boolean because spec p.66 supplies a default;
        # form_source is what tells a consumer how firm that boolean is.
        _job, preview, _diag = preview_of("fx_flex_form")
        data = {layer["name"]: layer for layer in
                step_to_json(_job, preview)["steps"][0]["layers"]}
        assert (data["FLEX_1"]["form"], data["FLEX_1"]["form_source"]) ==             ("FLEX", "DECLARED")
        assert data["FLEX_1"]["is_flex"] is True
        assert (data["GND_FLEX"]["form"], data["GND_FLEX"]["form_source"]) ==             ("FLEX", "FLEX_SUBTYPE")
        assert data["GND_FLEX"]["is_flex"] is True
        assert (data["SILK_1"]["form"], data["SILK_1"]["form_source"]) ==             ("RIGID", "DEFAULT")
        assert data["SILK_1"]["is_flex"] is False

    def test_layer_attributes_are_filtered_to_the_meaningful_ones(self):
        # AGENTS note 4: .layer_dielectric is meaningful only for non-copper
        # layers, and .copper_weight changes physical meaning with the unit
        # system, so both are surfaced as authored for the consumer to interpret.
        _job, preview, _diag = preview_of("fx_negative_layer")
        for layer in preview.layers:
            attributes = layer.layer_attributes()
            assert all(key.startswith(".") for key in attributes)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

class TestSelection:
    def test_a_layer_filter_narrows_the_result(self):
        _job, preview, _diag = preview_of(
            "minimal-test-odb", options=PreviewOptions(layers=["top"]))
        assert [l.name for l in preview.layers] == ["top"]

    def test_the_filter_is_case_insensitive(self):
        _job, preview, _diag = preview_of(
            "minimal-test-odb", options=PreviewOptions(layers=["TOP"]))
        assert [l.name for l in preview.layers] == ["top"]

    def test_an_unknown_layer_is_an_error_not_silence(self):
        # An empty export and a typo produce identical output, and want opposite
        # responses.
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("minimal-test-odb")), diagnostics=diagnostics)
        preview = build_step_preview(
            job, options=PreviewOptions(layers=["nope"]), diagnostics=diagnostics)
        assert preview.layers == []
        assert "export.unknown_layer" in diagnostics.codes()
        assert diagnostics.errors

    def test_excluding_a_type_removes_it(self):
        _job, preview, _diag = preview_of(
            "fx_negative_layer",
            options=PreviewOptions(exclude_types=("POWER_GROUND",)))
        assert all(l.type_name != "POWER_GROUND" for l in preview.layers)

    def test_type_exclusion_is_case_insensitive(self):
        _job, preview, _diag = preview_of(
            "fx_negative_layer",
            options=PreviewOptions(exclude_types=("power_ground",)))
        assert all(l.type_name != "POWER_GROUND" for l in preview.layers)


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------

class TestStreaming:
    def test_iter_matches_build(self):
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("minimal-test-odb")), diagnostics=diagnostics)
        streamed = {l.name: (len(l.rings), l.area_mm2)
                    for l in iter_step_previews(job, job.single_step,
                                                diagnostics=diagnostics)}
        built = {l.name: (len(l.rings), l.area_mm2)
                 for l in build_step_preview(job, diagnostics=diagnostics).layers}
        assert streamed == built

    def test_iter_yields_in_matrix_row_order(self):
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("fx_negative_layer")), diagnostics=diagnostics)
        rows = [l.layer.ref.row
                for l in iter_step_previews(job, job.single_step,
                                            diagnostics=diagnostics)]
        assert rows == sorted(rows)


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_a_broken_layer_profile_does_not_stop_the_other_layers(self):
        # AGENTS constraint 3 makes an unusable layer profile a hard error, but
        # hard for *that layer*: one bad layer should not cost the whole preview.
        diagnostics = Diagnostics()
        job = load_job(str(fixture_path("fx_bad_layer_profile")),
                       diagnostics=diagnostics)
        preview = build_step_preview(job, diagnostics=diagnostics)

        assert "DIELECTRIC_1" not in [l.name for l in preview.layers]
        assert preview.failed_layers == ["DIELECTRIC_1"]
        assert preview.empty_layers == []
        assert any(l.name == "F.CU" and not l.is_empty for l in preview.layers)

    def test_the_skip_is_reported_under_its_own_code(self):
        # Distinct from the loader's layer_profile.invalid, so neither message is
        # a restatement of the other: the loader says why, this says what was lost.
        diagnostics = Diagnostics()
        job = load_job(str(fixture_path("fx_bad_layer_profile")),
                       diagnostics=diagnostics)
        build_step_preview(job, diagnostics=diagnostics)
        codes = diagnostics.codes()
        assert "layer_profile.invalid" in codes
        assert "export.layer_skipped" in codes

    def test_the_underlying_error_is_still_raisable(self):
        # build_step_preview swallows it per layer; the model must still refuse.
        job = load_job(str(fixture_path("fx_bad_layer_profile")))
        with pytest.raises(LayerProfileError):
            job.single_step.base_geometry(job.single_step.layer("DIELECTRIC_1"))

    def test_a_preview_with_nothing_in_it_is_reported(self):
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("minimal-test-odb")), diagnostics=diagnostics)
        build_step_preview(job, options=PreviewOptions(layers=["COMP_+_TOP"]),
                           diagnostics=diagnostics)
        assert "export.no_geometry" in diagnostics.codes()


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------

class TestRealCases:
    def test_the_smoke_board_has_pads_at_distinct_positions(self):
        # The pads-not-translated bug produced one blob at the origin while every
        # count and the total area looked entirely plausible.
        _job, preview, _diag = preview_of("minimal-test-odb")
        top = preview.layer("top")
        assert top is not None
        x0, y0, x1, y1 = top.bounds
        assert (x1 - x0) > 50.0 and (y1 - y0) > 50.0
        # 22 rings from 23 records: the pads are genuinely spread out and the
        # copper does not collapse into a single blob.
        assert len(top.rings) > 15

    def test_an_inch_job_produces_metric_coordinates(self):
        # cases/designodb-rigidflex is INCH; a 1.6 mm board edge would be 0.063 in.
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("designodb-rigidflex.tgz")),
                       diagnostics=diagnostics)
        assert job.units.system.value == "INCH"
        preview = build_step_preview(job, diagnostics=diagnostics)
        x0, y0, x1, y1 = preview.bounds
        assert 20.0 < (x1 - x0) < 400.0
        assert abs(x1) < 1000.0

    def test_a_dielectric_layer_is_the_only_kind_that_uses_the_profile(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        for layer in preview.layers:
            if layer.type_name == "DIELECTRIC":
                assert layer.geometry.used_base, layer.name
            else:
                assert not layer.geometry.used_base, layer.name


def test_layer_summary_lines_mentions_every_layer():
    _job, preview, _diag = preview_of("minimal-test-odb")
    text = layer_summary_lines(preview)
    for layer in preview.layers:
        assert layer.name in text


# ---------------------------------------------------------------------------
# stackup and attribute semantics in the document
# ---------------------------------------------------------------------------

class TestStackupJson:
    """AGENTS constraint 3, and AGENTS note 4's two unit semantics.

    The JSON is the contract: it has to carry enough to rebuild the stack, and
    every attribute-derived number has to state what it was interpreted as.
    """

    def test_every_layer_states_the_scope_of_its_attributes(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        for layer in document["steps"][0]["layers"]:
            units = layer["attribute_units"]
            assert units is not None
            assert units["system"] in ("MM", "INCH")
            assert units["origin"]

    def test_the_stack_indexes_copper_and_spans_dielectrics(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        layers = {l["name"]: l for l in document["steps"][0]["layers"]}
        assert layers["F.CU"]["copper_index"] == 0
        assert layers["B.CU"]["copper_index"] == 1
        dielectric = layers["DIELECTRIC_1"]
        assert dielectric["z_span"] == {"from_layer": "F.CU", "to_layer": "B.CU",
                                        "copper_index": [0, 1]}
        # The step-level summary lists the same stack in row order.
        stackup = document["steps"][0]["stackup"]
        assert [s["name"] for s in stackup][:7] == [
            "COMP_+_TOP", "F.SILKSCREEN", "F.PASTE", "F.MASK", "F.CU",
            "DIELECTRIC_1", "B.CU"]
        assert stackup[4]["copper_index"] == 0

    def test_a_drill_states_its_endpoints_and_span(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        drill = next(l for l in document["steps"][0]["layers"]
                     if l["type"] == "DRILL")
        assert drill["start_layer"] == "F.CU"
        assert drill["end_layer"] == "B.CU"
        assert drill["z_span"]["copper_index"] == [0, 1]

    def test_a_metric_thickness_is_millimetres(self):
        # kicad-kitchen-sink is an MM job: 1.51 is 1.51 mm, not 38 um.
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        dielectric = next(l for l in document["steps"][0]["layers"]
                          if l["name"] == "DIELECTRIC_1")
        assert dielectric["thickness_mm"] == pytest.approx(1.51)
        assert dielectric["thickness"]["attribute_units"] == "MM"

    def test_an_imperial_thickness_is_converted_to_millimetres(self):
        # The review's exact example: designodb-rigidflex DIELECTRIC_3 declares
        # UNITS=INCH and .layer_dielectric=0.00255906, i.e. 65 um.  Under the
        # old export the string travelled through untouched next to units: mm.
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("designodb-rigidflex.tgz")),
                       diagnostics=diagnostics)
        preview = build_step_preview(
            job, options=PreviewOptions(layers=["DIELECTRIC_3"]),
            diagnostics=diagnostics)
        document = step_to_json(job, preview)
        dielectric = document["steps"][0]["layers"][0]
        assert dielectric["attribute_units"]["system"] == "INCH"
        assert dielectric["thickness_mm"] == pytest.approx(0.065, abs=1e-6)
        provenance = dielectric["thickness"]
        assert provenance["raw"] == pytest.approx(0.00255906)
        assert provenance["value_mm"] == pytest.approx(0.065000124)

    def test_the_dielectric_no_longer_covers_the_crossing_drill(self):
        # The whole point of constraint 3: the drill layer is a separate layer,
        # but the dielectric between the planes it crosses has its holes.
        from odbpp import clipper

        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        layers = {l["name"]: l for l in document["steps"][0]["layers"]}
        dielectric = layers["DIELECTRIC_1"]
        drill = next(l for l in layers.values() if l["type"] == "DRILL")

        assert dielectric["drills_subtracted"] == [drill["name"]]
        assert dielectric["drill_cut_mm2"] > 0.0

        def region(layer):
            return clipper.region_from_rings(
                [(p["points"], not p["island"]) for p in layer["polygons"]])

        assert clipper.intersection(region(dielectric), region(drill)).area \
            == pytest.approx(0.0, abs=1e-9)


class TestSerialisedTopology:
    """Coordinates are written only after the geometry was validated at them."""

    def test_every_written_ring_is_a_valid_non_degenerate_polygon(self):
        from shapely.geometry import Polygon

        for case in ("minimal-test-odb", "kicad-kitchen-sink"):
            _job, preview, _diag = preview_of(case)
            document = step_to_json(_job, preview)
            for layer in document["steps"][0]["layers"]:
                for points in (p["points"] for p in layer["polygons"]):
                    polygon = Polygon(points)
                    assert polygon.is_valid, (case, layer["name"])
                    assert polygon.area > 0.0, (case, layer["name"])

    def test_serialised_coordinates_are_exact_multiples_of_the_resolution(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        document = step_to_json(_job, preview, round_digits=4)
        for layer in document["steps"][0]["layers"]:
            for polygon in layer["polygons"]:
                for x, y in polygon["points"]:
                    assert abs(x * 10_000 - round(x * 10_000)) < 1e-6
                    assert abs(y * 10_000 - round(y * 10_000)) < 1e-6

    def test_reported_area_is_computed_from_the_written_coordinates(self):
        from shapely.geometry import Polygon

        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        document = step_to_json(_job, preview)
        for layer in document["steps"][0]["layers"]:
            net = 0.0
            for polygon in layer["polygons"]:
                area = Polygon(polygon["points"]).area
                net += area if polygon["island"] else -area
            if layer["polygons"]:
                assert layer["area_mm2"] == pytest.approx(net, abs=0.01), layer["name"]
