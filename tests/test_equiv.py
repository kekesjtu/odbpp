"""Material equivalence: formulas, grid, rectification, drills and materials.

Most tests build the ``odbpp/preview-2`` document directly, because that document
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
    CSV_COLUMNS,
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
    """A minimal but valid ``odbpp/preview-2`` document."""
    profile = profile if profile is not None else rect(0, 0, 10, 10)
    domain_list = domains or [{
        "id": "step_profile", "source": "step_profile", "layer": None,
        "polygons": [profile],
    }]
    layer_docs = []
    stackup = []
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
        stackup.append({"name": spec["name"], "type": spec.get("type", "SIGNAL"),
                        "z_mm": extent})
    return {"schema": "odbpp/preview-2", "units": "mm",
            "steps": [{"name": "pcb", "domains": domain_list,
                       "layers": layer_docs, "stackup": stackup}]}


def run(document, *, px=10, py=10, nx=None, ny=None, tolerance=0.01,
        materials=None, diagnostics=None, warn_partial=False):
    options = EquivalenceOptions(
        grid=GridOptions(px=px, py=py, nx=nx, ny=ny),
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
        result = run(only_layer(polygons=[rect(0, 5, 10, 10)]), nx=1, ny=1)
        cell = result.cells[0]
        assert cell.copper_fraction == pytest.approx(0.5)
        assert cell.kx == pytest.approx(PARALLEL)
        assert cell.ky == pytest.approx(HARMONIC)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_a_vertical_split_swaps_the_directions(self):
        result = run(only_layer(polygons=[rect(0, 0, 5, 10)]), nx=1, ny=1)
        cell = result.cells[0]
        assert cell.kx == pytest.approx(HARMONIC)
        assert cell.ky == pytest.approx(PARALLEL)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_a_checkerboard_is_series_in_both_plane_directions(self):
        result = run(only_layer(polygons=[rect(0, 0, 5, 5), rect(5, 5, 10, 10)]),
                     nx=1, ny=1)
        cell = result.cells[0]
        assert cell.kx == pytest.approx(HARMONIC)
        assert cell.ky == pytest.approx(HARMONIC)
        assert cell.kz == pytest.approx(PARALLEL)

    def test_density_is_volume_weighted_and_specific_heat_mass_weighted(self):
        result = run(only_layer(polygons=[rect(0, 5, 10, 10)]), nx=1, ny=1)
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

    def test_a_drill_is_overlaid_on_copper_too(self):
        document = build_document(layers=[
            {"name": "CU", "type": "SIGNAL", "polygons": []},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)]},
        ])
        result = run(document)
        drilled = [c for c in result.cells if c.copper_fraction > 0]
        assert drilled and all(c.copper_fraction == pytest.approx(1.0)
                               for c in drilled)

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
            {"name": "CU", "type": "SIGNAL", "polygons": []},
            {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)],
             "z_mm": None},
        ])
        # Force the unresolvable span rather than the synthetic default.
        document["steps"][0]["stackup"][1]["z_mm"] = None
        diagnostics = Diagnostics()
        result = run(document, diagnostics=diagnostics)
        assert result.fatal
        assert "equiv.drill_span_unknown" in diagnostics.codes()


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
        document["steps"][0]["stackup"].append({
            "name": "DIEL", "type": "DIELECTRIC",
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


class TestSchema:
    def test_preview_1_is_rejected_because_it_has_no_domains(self):
        document = build_document()
        document["schema"] = "odbpp/preview-1"
        with pytest.raises(EquivalenceError):
            run(document)

    def test_an_unknown_schema_is_rejected(self):
        document = build_document()
        document["schema"] = "something/else"
        with pytest.raises(EquivalenceError):
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
        # Only the base phase still falls back.
        assert [d.code for d in diagnostics.items] == ["materials.default_used"]

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


class TestAutoTolerance:
    def test_the_default_tolerance_follows_the_pixel_pitch(self):
        document = only_layer(profile=rect(0, 0, 40, 20),
                              polygons=[rect(0, 0, 40, 20)])
        result = equivalence_from_document(
            document, EquivalenceOptions(grid=GridOptions(px=40, py=40)))
        # pitch_x = 40/40 = 1.0, pitch_y = 20/40 = 0.5 -> twice the coarser pitch.
        assert result.rectification.tolerance_mm == pytest.approx(2.0)
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
    def test_csv_and_metadata_are_written(self, tmp_path):
        result = run(only_layer(polygons=[rect(0, 0, 10, 10)]))
        csv_path = tmp_path / "board.MaterialMap.csv"
        meta_path = tmp_path / "board.MaterialMap.meta.json"
        write_materialmap(result, str(csv_path), str(meta_path),
                          source_path="x", source_schema="odbpp/preview-2",
                          step_name="pcb")
        header = csv_path.read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == CSV_COLUMNS
        assert len(csv_path.read_text(encoding="utf-8").splitlines()) == \
            1 + len(result.cells)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["schema"] == "odbpp/materialmap-1"
        assert meta["units"]["thermal_conductivity"] == "W/(m*K)"
        assert meta["drill_model"] == "solid_copper"
        assert meta["z_axis"].startswith("increases downward")
        assert "rectification" in meta and "materials" in meta
        assert meta["materials"]["by_layer"]

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
        result = run(document, px=20, py=20)
        assert not result.fatal
        assert result.layers and all(r.status == "equivalent"
                                     for r in result.layers)
        assert result.cells
        for cell in result.cells:
            assert min(K1, K2) - 1e-6 <= cell.kx <= max(K1, K2) + 1e-6

    def test_kitchen_sink_dielectric_picks_up_its_drills(self):
        document = self._document_for("kicad-kitchen-sink")
        result = run(document, px=500, py=500, tolerance=1.0)
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
                     "--px", "10", "--py", "10", "-o", str(tmp_path)])
        assert code == 0
        csv_path = tmp_path / "pcb.MaterialMap.csv"
        meta_path = tmp_path / "pcb.MaterialMap.meta.json"
        assert csv_path.is_file() and meta_path.is_file()
        rows = csv_path.read_text(encoding="utf-8").splitlines()
        assert rows[0].split(",") == CSV_COLUMNS
        assert len(rows) > 1

    def test_from_json_consumes_an_existing_preview(self, tmp_path):
        from odbpp.cli import main

        preview = tmp_path / "pcb.json"
        assert main(["export", str(case_path("minimal-test-odb")),
                     "-o", str(tmp_path), "--only-json"]) == 0
        code = main(["equivalent", "--from-json", str(preview),
                     "--px", "8", "--py", "8", "-o", str(tmp_path / "eq")])
        assert code == 0
        assert (tmp_path / "eq" / "pcb.MaterialMap.csv").is_file()

    def test_nx_beyond_px_is_rejected(self, tmp_path):
        from odbpp.cli import main

        code = main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "10", "--py", "10", "--nx", "11",
                     "-o", str(tmp_path)])
        assert code == 2
        assert not list(tmp_path.glob("*.csv"))

    def test_px_py_are_required(self):
        from odbpp.cli import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["equivalent", "some/path"])
