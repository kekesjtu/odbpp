"""Material equivalence: formulas, grid, rectification, drills and materials.

Most tests build the ``current intermediate`` document directly, because that document
*is* the interface between the parser and the equivalence (AGENTS: 中间 JSON 是
解析器与等效器之间唯一的数据接口).  A few use the real cases to make sure the
document the parser actually writes is accepted.
"""

from __future__ import annotations

import json
import math

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.equiv import (
    EquivalenceError,
    EquivalenceOptions,
    equivalence_from_document,
    write_materialmap,
)
from odbpp.grid import GridOptions, build_grid
from odbpp.materials import (
    DEFAULT_BASE,
    DEFAULT_COPPER,
    MaterialBook,
    PROJECT_DEFAULTS,
)
from odbpp.rectify import Rect, RectifyOptions, rectify_region
from odbpp.clipper import region_from_rings

from conftest import case_path


# ---------------------------------------------------------------------------
# synthetic preview documents
# ---------------------------------------------------------------------------

def rect(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": True,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def hole(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": False,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def build_document(*, profile=None, layers=(), domains=None, z=0.035) -> dict:
    """A minimal but valid ``current intermediate`` document."""
    profile = profile if profile is not None else rect(0, 0, 10, 10)
    domain_list = domains or [{
        "id": "step_profile", "source": "step_profile",         "polygons": [profile],
    }]
    layer_docs = []
    for spec in layers:
        extent = spec.get("z_mm")
        if extent is None:
            extent = {"start_mm": 0.0, "end_mm": z, "thickness_known": True}
        layer_docs.append({
            "name": spec["name"],
            "type": spec.get("type", "SIGNAL"),
            "polygons": spec.get("polygons", []),
            "domain_id": spec.get("domain_id", "step_profile"),
            "z_mm": extent,
        })
    return {"units": "mm",
            "steps": [{"name": "pcb", "domains": domain_list,
                       "layers": layer_docs, "profile_domain_id": "step_profile"}]}


def run(document, *, px=10, py=10, max_cell_mm=None, tolerance=0.01,
        materials=None, diagnostics=None, warn_partial=False):
    options = EquivalenceOptions(
        grid=GridOptions(px=px, py=py, min_cell_mm=0.001,
                         max_cell_mm=max_cell_mm or (10/px, 10/py)),
        materials=materials or MaterialBook(diagnostics=diagnostics),
        rectify=RectifyOptions(tolerance_mm=tolerance),
        warn_partial_domains=warn_partial,
    )
    return equivalence_from_document(document, options, diagnostics=diagnostics)


def only_layer(*, type_name="SIGNAL", polygons=(), profile=None, z=0.035):
    return build_document(profile=profile,
                          layers=[{"name": "L1", "type": type_name,
                                   "polygons": list(polygons), "z_mm": {
                                       "start_mm": 0.0, "end_mm": z,
                                       "thickness_known": True}}])


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------

K1 = DEFAULT_COPPER.k_w_mk
K2 = DEFAULT_BASE.k_w_mk
RHO1 = DEFAULT_COPPER.density_kg_m3
RHO2 = DEFAULT_BASE.density_kg_m3
C1 = DEFAULT_COPPER.specific_heat_j_kgk
C2 = DEFAULT_BASE.specific_heat_j_kgk
HARMONIC = 2 * K1 * K2 / (K1 + K2)
PARALLEL = (K1 + K2) / 2


class TestFormulas:
    """The three directions and the two mass properties from the derivation."""

    def test_all_copper_reproduces_the_copper_phase(self):
        result = run(only_layer(polygons=[rect(0, 0, 10, 10)]))
        for cell in result.cells:
            assert cell.copper_fraction == pytest.approx(1.0)
            assert cell.kx == pytest.approx(K1)
            assert cell.ky == pytest.approx(K1)
            assert cell.kz == pytest.approx(K1)
            assert cell.density == pytest.approx(RHO1)
            assert cell.specific_heat == pytest.approx(C1)

    def test_all_base_reproduces_the_base_phase(self):
        result = run(only_layer(polygons=[]))
        for cell in result.cells:
            assert cell.copper_fraction == 0.0
            assert cell.kx == pytest.approx(K2)
            assert cell.ky == pytest.approx(K2)
            assert cell.kz == pytest.approx(K2)
            assert cell.density == pytest.approx(RHO2)
            assert cell.specific_heat == pytest.approx(C2)

    def test_a_horizontal_split_is_parallel_in_x_and_series_in_y(self):
        # One cell covering the whole board: copper on top, base below.  Every
        # pixel row is uniform, so x is parallel; every pixel column is half and
        # half in series, so y is the harmonic mean.
        result = run(only_layer(polygons=[rect(0, 5, 10, 10)]), max_cell_mm=10)
        cell = result.cells[0]
        assert cell.copper_fraction == pytest.approx(0.5)
        assert cell.kx == pytest.approx(PARALLEL)
        assert cell.ky == pytest.approx(HARMONIC)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_a_vertical_split_swaps_the_directions(self):
        result = run(only_layer(polygons=[rect(0, 0, 5, 10)]), max_cell_mm=10)
        cell = result.cells[0]
        assert cell.kx == pytest.approx(HARMONIC)
        assert cell.ky == pytest.approx(PARALLEL)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_a_checkerboard_is_series_in_both_plane_directions(self):
        result = run(only_layer(polygons=[rect(0, 0, 5, 5), rect(5, 5, 10, 10)]),
                     max_cell_mm=10)
        cell = result.cells[0]
        assert cell.kx == pytest.approx(HARMONIC)
        assert cell.ky == pytest.approx(HARMONIC)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_density_is_volume_weighted_and_specific_heat_mass_weighted(self):
        result = run(only_layer(polygons=[rect(0, 5, 10, 10)]), max_cell_mm=10)
        cell = result.cells[0]
        expected_density = 0.5 * RHO1 + 0.5 * RHO2
        expected_cp = (0.5 * RHO1 * C1 + 0.5 * RHO2 * C2) / expected_density
        assert cell.density == pytest.approx(expected_density)
        assert cell.specific_heat == pytest.approx(expected_cp)

    def test_every_property_is_finite_and_between_the_two_phases(self):
        result = run(only_layer(polygons=[rect(0, 0, 5, 5), rect(7, 2, 9, 6)]),
                     px=10, py=10)
        assert result.cells
        for cell in result.cells:
            for value in (cell.kx, cell.ky, cell.kz):
                assert math.isfinite(value)
                assert min(K1, K2) - 1e-9 <= value <= max(K1, K2) + 1e-9
            assert min(RHO1, RHO2) - 1e-9 <= cell.density <= max(RHO1, RHO2) + 1e-9
            assert min(C1, C2) - 1e-9 <= cell.specific_heat <= max(C1, C2) + 1e-9


class TestPixelSampling:
    def test_a_cell_with_no_pixel_centre_is_reported(self):
        # L-shaped board, 2x2 raster: the 1 mm-wide tab between x=0 and x=1
        # falls between two pixel centres along x, so it cannot be sampled.
        profile = {"island": True,
                   "points": [[0, 0], [10, 0], [10, 5], [1, 5], [1, 10],
                              [0, 10]]}
        document = only_layer(polygons=[], profile=profile)
        diagnostics = Diagnostics()
        result = run(document, px=2, py=2, diagnostics=diagnostics)
        assert result.fatal
        assert "grid.cell_without_pixel" in diagnostics.codes()


class TestPartialDomain:
    def test_a_partial_cell_reports_coverage_instead_of_a_full_cell(self):
        # The shared grid cell is the whole 10x10 board, but the layer's own
        # computational domain covers only the left half.  The CSV must not
        # imply the cell is solid FR4 over its full area.
        domains = [
            {"id": "step_profile", "source": "step_profile",              "polygons": [rect(0, 0, 10, 10)]},
            {"id": "half", "source": "layer_profile", "polygons": [rect(0, 0, 5, 10)]},
        ]
        document = build_document(domains=domains, layers=[
            {"name": "CU", "type": "SIGNAL", "domain_id": "half",
             "polygons": []}])
        diagnostics = Diagnostics()
        result = run(document, px=10, py=10, max_cell_mm=10,
                     diagnostics=diagnostics, warn_partial=True)
        cell = result.cells[0]
        assert cell.x0 == 0.0 and cell.x1 == pytest.approx(5.0)
        assert cell.cell_area_mm2 == pytest.approx(50.0)
        assert cell.domain_coverage == pytest.approx(1.0)
        assert cell.domain_area_mm2 == pytest.approx(50.0)
        assert cell.partial_domain is False
        assert "equiv.partial_domain" not in diagnostics.codes()

    def test_partial_coverage_is_warned_by_default(self):
        domains = [
            {"id": "step_profile", "source": "step_profile",              "polygons": [rect(0, 0, 10, 10)]},
            {"id": "half", "source": "layer_profile", "polygons": [rect(0, 0, 5, 10)]},
        ]
        document = build_document(domains=domains, layers=[
            {"name": "CU", "type": "SIGNAL", "domain_id": "half",
             "polygons": []}])
        diagnostics = Diagnostics()
        options = EquivalenceOptions(
            grid=GridOptions(px=10, py=10, max_cell_mm=10),
            materials=MaterialBook(diagnostics=diagnostics),
            rectify=RectifyOptions(tolerance_mm=0.01),
        )
        assert options.warn_partial_domains is True
        equivalence_from_document(document, options, diagnostics=diagnostics)
        assert "equiv.partial_domain" not in diagnostics.codes()

    def test_a_fully_covered_cell_has_unit_coverage(self):
        result = run(only_layer(polygons=[rect(0, 0, 10, 10)]), max_cell_mm=10)
        cell = result.cells[0]
        assert cell.domain_coverage == pytest.approx(1.0)
        assert cell.partial_domain is False
        assert cell.domain_area_mm2 == pytest.approx(cell.cell_area_mm2)


# ---------------------------------------------------------------------------
# drills
# ---------------------------------------------------------------------------

class TestDrillProjection:
    def test_a_drill_through_a_dielectric_becomes_solid_copper(self):
        document = build_document(layers=[
            {"name": "DIEL", "type": "DIELECTRIC", "polygons": []},
            {"name": "DRILL", "type": "DRILL",
             "polygons": [rect(4, 4, 6, 6)]},
        ])
        result = run(document)
        drilled = [c for c in result.cells
                   if c.layer_name == "DIEL" and c.copper_fraction > 0]
        assert drilled
        for cell in drilled:
            assert cell.copper_fraction == pytest.approx(1.0)
            assert cell.kz == pytest.approx(K1)
            assert cell.density == pytest.approx(RHO1)
        untouched = [c for c in result.cells if c.layer_name == "DIEL"
                     and c.copper_fraction == 0]
        assert untouched and untouched[0].kz == pytest.approx(K2)

    def test_a_drill_never_adds_copper_to_a_copper_layer(self):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": []},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)]},
        ])
        result = run(document)
        drilled = [c for c in result.cells if c.copper_fraction > 0]
        assert not drilled
        assert next(r for r in result.layers if r.name == "CU").drill_overlays == []

    def test_a_drill_outside_the_z_interval_is_not_projected(self):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": [],
             "z_mm": {"start_mm": 0.0, "end_mm": 0.035, "thickness_known": True}},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)],
             "z_mm": {"start_mm": 5.0, "end_mm": 6.0, "thickness_known": True}},
        ])
        result = run(document)
        assert all(c.copper_fraction == 0.0 for c in result.cells)

    def test_an_unresolvable_drill_span_is_fatal_never_a_through_hole(self):
        document = build_document(layers=[
            {"name": "CU", "type": "DIELECTRIC", "polygons": []},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)],
             "z_mm": None},
        ])
        # Force the unresolvable span rather than the synthetic default.
        document["steps"][0]["layers"][1]["z_mm"] = None
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        assert result.fatal
        assert "equiv.drill_span_unknown" in diagnostics.codes()

    def test_a_blind_drill_affects_only_its_dielectric_interval(self):
        document = build_document(layers=[
            {"name": "C1", "polygons": []}, {"name": "C2", "polygons": []},
            {"name": "C3", "polygons": []},
            {"name": "UPPER", "type": "DIELECTRIC", "polygons": [],
             "z_mm": {"start_mm": 0.0, "end_mm": 0.0, "thickness_known": False}},
            {"name": "LOWER", "type": "DIELECTRIC", "polygons": [],
             "z_mm": {"start_mm": 0.0, "end_mm": 0.0, "thickness_known": False}},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4,4,6,6)]},
        ])
        layers = document["steps"][0]["layers"]
        for index, layer in enumerate(layers[:3]):
            layer["copper_index"] = index
        layers[3]["copper_span"] = [0,1]
        layers[4]["copper_span"] = [1,2]
        layers[5]["copper_span"] = [0,1]
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        assert not result.fatal
        for name in ("C1", "C2", "C3", "LOWER"):
            assert all(c.copper_fraction == 0 for c in result.cells if c.layer_name == name)
        assert any(c.copper_fraction > 0 for c in result.cells if c.layer_name == "UPPER")
        assert "equiv.drill_z_mismatch" in diagnostics.codes()


# ---------------------------------------------------------------------------
# layers and schema
# ---------------------------------------------------------------------------

class TestLayerSelection:
    def test_only_copper_and_dielectric_are_equivalented(self):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "MASK", "type": "SOLDER_MASK", "polygons": [rect(0, 0, 1, 1)]},
            {"name": "DOC", "type": "DOCUMENT", "polygons": []},
        ])
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        statuses = {r.name: r.status for r in result.layers}
        assert statuses["CU"] == "equivalent"
        assert statuses["MASK"] == "skipped"
        assert statuses["DOC"] == "skipped"
        assert "equiv.layer_skipped" in diagnostics.codes()
        reasons = {r.name: r.reason for r in result.layers}
        assert "copper/FR4" in reasons["MASK"]

    def test_a_candidate_absent_from_the_preview_is_reported(self):
        document = build_document(layers=[])
        document["steps"][0]["layers"].append({
            "name": "DIEL", "type": "DIELECTRIC", "polygons": [],
            "geometry_status": "failed", "geometry_error": "invalid layer profile",
            "z_mm": {"start_mm": 0.0, "end_mm": 0.035, "thickness_known": True},
        })
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        assert "equiv.layer_failed" in diagnostics.codes()
        report = next(r for r in result.layers if r.name == "DIEL")
        assert report.status == "failed"

    def test_a_missing_computational_domain_blocks_the_layer(self):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": [rect(0, 0, 10, 10)]},
        ])
        document["steps"][0]["layers"][0]["domain_id"] = None
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        assert "equiv.missing_domain" in diagnostics.codes()
        assert result.layers[0].status == "failed"


class TestContract:
    def test_missing_required_structure_is_rejected(self):
        document = build_document()
        del document["steps"][0]["domains"]
        with pytest.raises(EquivalenceError, match="domains"):
            run(document)

    def test_unknown_field_is_reported_without_version_negotiation(self):
        document = build_document()
        document["unexpected"] = 42
        with pytest.raises(EquivalenceError, match="unexpected"):
            run(document)


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

class TestMaterials:
    def test_defaults_have_recorded_sources(self):
        for material in PROJECT_DEFAULTS.values():
            assert material.source
            assert material.k_w_mk > 0
            assert material.density_kg_m3 > 0
            assert material.specific_heat_j_kgk > 0

    def test_falling_back_to_defaults_warns(self):
        diagnostics = Diagnostics()
        book = MaterialBook(diagnostics=diagnostics)
        book.resolve("L1")
        warnings = [d for d in diagnostics.items
                    if d.code == "materials.default_used"]
        assert len(warnings) == 2  # one per phase

    def test_a_global_override_wins_over_the_default(self):
        diagnostics = Diagnostics()
        book = MaterialBook({"global": {"copper": {"k_w_mk": 400.0}}},
                            diagnostics=diagnostics)
        pair = book.resolve("L1")
        assert pair.k1 == 400.0
        assert pair.k2 == pytest.approx(DEFAULT_BASE.k_w_mk)
        assert pair.provenance["copper"]["origin"] == "user_global"
        # Copper's k is the user's, but its density and cp still fall back, so
        # both phases raise the per-field default warning.
        warnings = [d for d in diagnostics.items
                    if d.code == "materials.default_used"]
        assert len(warnings) == 2
        assert "density_kg_m3" in warnings[0].message
        assert "k_w_mk" not in warnings[0].message
        copper_fields = pair.provenance["copper"]["fields"]
        assert copper_fields["k_w_mk"]["origin"] == "user_global"
        assert copper_fields["density_kg_m3"]["origin"] == "project_default"

    def test_a_layer_override_wins_over_a_global_override(self):
        book = MaterialBook({
            "global": {"copper": {"k_w_mk": 400.0}},
            "layers": {"L1": {"copper": {"k_w_mk": 350.0}}},
        })
        assert book.resolve("L1").k1 == 350.0
        assert book.resolve("L2").k1 == 400.0

    def test_a_bad_material_field_is_an_error_with_a_fallback(self):
        diagnostics = Diagnostics()
        book = MaterialBook({"global": {"copper": {"k_w_mk": "hot"}}},
                            diagnostics=diagnostics)
        assert "materials.invalid" in diagnostics.codes()
        assert book.resolve("L1").k1 == pytest.approx(DEFAULT_COPPER.k_w_mk)

    def test_an_override_changes_the_equivalent_value(self):
        diagnostics = Diagnostics()
        document = only_layer(polygons=[rect(0, 0, 10, 10)])
        result = run(document, materials=MaterialBook(
            {"global": {"copper": {"k_w_mk": 200.0}}}, diagnostics=diagnostics))
        assert all(c.kx == pytest.approx(200.0) for c in result.cells)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"),
                                     float("-inf"), 0.0, -1.0])
    def test_a_non_finite_or_non_positive_override_is_rejected(self, bad):
        diagnostics = Diagnostics()
        book = MaterialBook({"global": {"copper": {"k_w_mk": bad}}},
                            diagnostics=diagnostics)
        assert "materials.invalid" in diagnostics.codes()
        value = book.resolve("L1").k1
        assert math.isfinite(value) and value > 0.0

    def test_a_non_finite_material_never_reaches_the_output(self):
        diagnostics = Diagnostics()
        document = only_layer(polygons=[rect(0, 0, 10, 10)])
        result = run(document, materials=MaterialBook(
            {"global": {"copper": {"k_w_mk": float("nan"),
                                     "density_kg_m3": float("inf")}}},
            diagnostics=diagnostics))
        assert result.cells and not result.fatal
        for cell in result.cells:
            for value in (cell.kx, cell.ky, cell.kz, cell.density,
                          cell.specific_heat):
                assert math.isfinite(value) and value > 0.0
        assert "materials.invalid" in diagnostics.codes()

    def test_provenance_is_recorded_per_field(self):
        book = MaterialBook({
            "global": {"copper": {"k_w_mk": 900.0}},
            "layers": {"L1": {"base": {"density_kg_m3": 1900.0}}},
        })
        pair = book.resolve("L1")
        copper = pair.provenance["copper"]["fields"]
        assert copper["k_w_mk"]["origin"] == "user_global"
        assert copper["k_w_mk"]["value"] == 900.0
        assert copper["density_kg_m3"]["origin"] == "project_default"
        base = pair.provenance["base"]["fields"]
        assert base["density_kg_m3"]["origin"] == "layer_override"
        assert base["k_w_mk"]["origin"] == "project_default"


class TestAutoTolerance:
    def test_the_default_tolerance_follows_physical_board_scale(self):
        document = only_layer(profile=rect(0, 0, 40, 20),
                              polygons=[rect(0, 0, 40, 20)])
        result = equivalence_from_document(
            document, EquivalenceOptions(grid=GridOptions(px=40, py=40)))
        # Physical shorter side / 32, independent of the sampling pixel pitch.
        assert result.rectification.tolerance_mm == pytest.approx(20 / 32)
        assert not result.fatal

    def test_a_coarse_raster_with_the_default_tolerance_still_samples(self):
        document = only_layer(profile=rect(0, 0, 40, 20),
                              polygons=[rect(0, 0, 40, 20)])
        result = equivalence_from_document(
            document, EquivalenceOptions(grid=GridOptions(px=25, py=25)))
        assert result.cell_without_pixel == 0
        assert result.cell_count > 0


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

class TestOutput:
    def test_single_json_contains_only_region_geometry_and_five_properties(self, tmp_path):
        from odbpp.materialmap import read_document, iter_cells
        result = run(only_layer(polygons=[rect(0, 0, 10, 10)]))
        path = tmp_path / "board.MaterialMap.json"
        write_materialmap(result, str(path), step_name="pcb")
        document = read_document(path)
        assert document.properties == ["kx", "ky", "kz", "density", "specific_heat"]
        assert len(list(iter_cells(document))) == len(result.cells)
        assert all(tuple(c.property_values()) == value for c, (_, _, value) in zip(result.cells, iter_cells(document)))
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw["regions"][0]) == {"layer", "bounds_mm", "x_lines_mm", "y_lines_mm", "values"}
        assert all(len(values) == 5 for r in raw["regions"] for values in r["values"])
        assert not list(tmp_path.glob("*.csv")) and not list(tmp_path.glob("*.meta.json"))

    def test_metadata_records_z_trust_and_thickness_source(self, tmp_path):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": [rect(0, 0, 10, 10)],
             "z_mm": {"start_mm": 0.0, "end_mm": 0.035,
                      "thickness_known": False, "source": ".copper_weight"}},
        ])
        result = run(document)
        meta = result.metadata(step_name="pcb")
        report = next(r for r in meta["layers"] if r["name"] == "CU")
        assert report["z_mm"]["thickness_known"] is False
        assert report["z_mm"]["start_mm"] == 0.0

    def test_metadata_carries_the_rectification_error(self):
        result = run(only_layer(polygons=[rect(0, 0, 10, 10)]))
        meta = result.metadata()
        assert meta["rectification"]["ok"] is True
        assert "shape_deviation_mm2" in meta["rectification"]

    def test_a_rectification_that_fills_a_hole_is_fatal(self):
        # A 0.2 mm slot cannot survive a 5 mm tolerance; filling it changes the
        # board shape, so the run must be marked untrustworthy, not written.
        domains = [{
            "id": "step_profile", "source": "step_profile",             "polygons": [rect(0, 0, 10, 10), rect(12, 0, 12.05, 10)],
        }]
        document = build_document(domains=domains, layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": []}])
        diagnostics = Diagnostics()
        result = equivalence_from_document(document, EquivalenceOptions(GridOptions(2000,2000)), diagnostics=diagnostics)
        assert not result.grid.cells
        assert result.fatal is True
        assert diagnostics.errors


# ---------------------------------------------------------------------------
# real cases
# ---------------------------------------------------------------------------

class TestRealCases:
    def _document_for(self, name: str) -> dict:
        from odbpp.export import PreviewOptions, build_step_preview, step_to_json
        from odbpp.job import load_job

        job = load_job(str(case_path(name)))
        preview = build_step_preview(job, options=PreviewOptions(round_digits=4))
        return step_to_json(job, preview)

    def test_minimal_case_round_trips_through_the_document(self):
        document = self._document_for("minimal-test-odb")
        result = run(document, px=20, py=20, max_cell_mm=5)
        assert not result.fatal
        assert result.layers and all(r.status == "equivalent"
                                     for r in result.layers)
        assert result.cells
        for cell in result.cells:
            assert min(K1, K2) - 1e-6 <= cell.kx <= max(K1, K2) + 1e-6

    def test_kitchen_sink_dielectric_picks_up_its_drills(self):
        document = self._document_for("kicad-kitchen-sink")
        result = run(document, px=2400, py=1800, tolerance=1.0, max_cell_mm=5)
        assert not result.fatal
        dielectric = [c for c in result.cells if c.layer_type == "DIELECTRIC"]
        assert dielectric
        assert any(c.copper_fraction > 0 for c in dielectric), \
            "the drill projection did not reach the dielectric layer"
        assert all(c.thickness_known for c in dielectric)


class TestCommandLine:
    def test_equivalent_writes_a_csv_and_metadata(self, tmp_path):
        from odbpp.cli import main

        code = main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "10", "--py", "10", "--max-cell-mm", "20", "-o", str(tmp_path)])
        assert code == 0
        csv_path = tmp_path / "pcb.MaterialMap.json"
        meta_path = tmp_path / "diagnostics/run.json"
        assert csv_path.is_file() and meta_path.is_file()
        data = json.loads(csv_path.read_text(encoding="utf-8"))
        assert data["regions"] and data["properties"] == ["kx", "ky", "kz", "density", "specific_heat"]

    def test_from_json_consumes_an_existing_preview(self, tmp_path):
        from odbpp.cli import main

        preview = tmp_path / "pcb.json"
        assert main(["export", str(case_path("minimal-test-odb")),
                     "-o", str(tmp_path), "--only-json"]) == 0
        code = main(["equivalent", "--from-json", str(preview),
                     "--px", "8", "--py", "8", "--max-cell-mm", "20", "-o", str(tmp_path / "eq")])
        assert code == 0
        assert (tmp_path / "eq" / "pcb.MaterialMap.json").is_file()

    def test_invalid_cell_size_bounds_are_rejected(self, tmp_path):
        from odbpp.cli import main

        code = main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "10", "--py", "10", "--min-cell-mm", "6", "--max-cell-mm", "5",
                     "-o", str(tmp_path)])
        assert code == 2
        assert not list(tmp_path.glob("*.MaterialMap.json"))

    def test_a_rectification_failure_writes_no_csv(self, tmp_path):
        from odbpp.cli import main

        domains = [{
            "id": "step_profile", "source": "step_profile",             "polygons": [rect(0, 0, 10, 10), rect(12, 0, 12.05, 10)],
        }]
        document = build_document(domains=domains, layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": []}])
        preview = tmp_path / "board.json"
        preview.write_text(json.dumps(document), encoding="utf-8")
        out = tmp_path / "eq"
        code = main(["equivalent", "--from-json", str(preview),
                     "--px", "20", "--py", "20",
                     "--min-cell-mm", "0.1", "-o", str(out)])
        assert code == 2
        assert not list(out.glob("*.MaterialMap.json"))
        assert not list(out.glob("*.tmp"))

    def test_px_py_default_to_dense_automatic_sampling(self):
        from odbpp.cli import build_parser

        args=build_parser().parse_args(["equivalent", "some/path"])
        assert args.px is None and args.py is None
        assert args.min_cell_mm is None and args.max_cell_mm is None
