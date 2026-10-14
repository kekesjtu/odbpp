"""The DRILL policy separates dielectric display/materials from copper artwork."""
from copy import deepcopy
import json

import pytest

from odbpp.drawing import dielectric_drill_cutouts
from odbpp.export import PreviewOptions, build_step_preview, layer_to_json, step_to_json
from odbpp.job import load_job
from odbpp.svg import render_html, render_svg, write_outputs
from odbpp.equiv import EquivalenceOptions, equivalence_from_document
from odbpp.grid import GridOptions
from odbpp.raster import LayerMask
from conftest import sample_path
from test_equiv import build_document, rect, hole
from test_expand import _drill_step
from test_svg import parse, layer_groups, path_of


def evaluate(doc):
    masks = {}
    result = equivalence_from_document(doc, EquivalenceOptions(
        GridOptions(20, 20, min_cell_mm=0.001, max_cell_mm=1)),
        mask_sink=lambda name, mask, pitch: masks.update({name: bytes(mask.pixels)}))
    assert not result.fatal, [d.message for d in result.diagnostics.errors]
    return result, masks


@pytest.mark.parametrize("kind", ["SIGNAL", "POWER_GROUND", "MIXED"])
def test_drill_leaves_copper_clearance_and_every_property_unchanged(kind):
    doc = build_document(layers=[
        {"name": "CU", "type": kind, "polygons": [rect(0, 0, 10, 10), hole(4, 4, 6, 6)]},
        {"name": "DIEL", "type": "DIELECTRIC", "polygons": [rect(0, 0, 10, 10)]},
        {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)]}])
    reference = deepcopy(doc)
    reference["steps"][0]["layers"].pop()
    after, masks = evaluate(doc)
    before, reference_masks = evaluate(reference)
    assert [c for c in after.cells if c.layer_name == "CU"] == [c for c in before.cells if c.layer_name == "CU"]
    assert after.grid.x_lines == before.grid.x_lines and after.grid.y_lines == before.grid.y_lines
    assert masks["CU"] == reference_masks["CU"]
    assert masks["CU"][10 * 20 + 10] == LayerMask.WHITE
    assert masks["DIEL"][10 * 20 + 10] == LayerMask.BLACK
    assert reference_masks["DIEL"][10 * 20 + 10] == LayerMask.WHITE
    assert all(not r.drill_overlays for r in after.layers if r.type != "DIELECTRIC")


@pytest.mark.parametrize("status", ["ready", "failed", "omitted"])
def test_copper_only_input_does_not_need_drill_span_or_geometry(status):
    doc = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]},
                                 {"name": "DRILL", "type": "DRILL"}])
    drill = doc["steps"][0]["layers"][1]
    drill.pop("z_mm")
    drill["geometry_status"] = status
    if status == "failed":
        drill["geometry_error"] = "synthetic failure"
    result, _ = evaluate(doc)
    assert not any(d.code == "equiv.drill_span_unknown" for d in result.diagnostics)


def test_profile_void_cannot_be_filled_by_a_dielectric_drill():
    domains = [{"id": "step_profile", "source": "step_profile",
                "polygons": [rect(0, 0, 10, 10), hole(4, 4, 6, 6)]}]
    doc = build_document(domains=domains, layers=[
        {"name": "DIEL", "type": "DIELECTRIC", "polygons": domains[0]["polygons"]},
        {"name": "DRILL", "type": "DRILL", "polygons": [rect(3, 3, 7, 7)]}])
    result, masks = evaluate(doc)
    assert masks["DIEL"][10 * 20 + 10] == LayerMask.OUTSIDE
    assert masks["DIEL"][6 * 20 + 6] == LayerMask.BLACK
    assert all(not (c.x0 < 5 < c.x1 and c.y0 < 5 < c.y1) for c in result.cells)


def preview(*, filtered=False):
    from odbpp.clipper import region_from_rings, to_surface_geometry
    from odbpp.features import FeatureFile, SurfaceFeature
    from odbpp.units import UnitScope
    step = _drill_step(include=("D_1_2",))
    geometry = to_surface_geometry(region_from_rings([
        ([(0, 0), (10, 0), (10, 10), (0, 10)], False),
        ([(4, 4), (4, 6), (6, 6), (6, 4)], True)]))
    step.layer("SIGNAL_1").features = FeatureFile(path="copper", units=UnitScope.metric(),
        records=[SurfaceFeature(line_no=1, geometry=geometry)])
    job = load_job(str(sample_path("minimal-test-odb")))
    options = PreviewOptions(layers=["SIGNAL_1", "DIELECTRIC_A"] if filtered else None)
    return job, build_step_preview(job, step=step, options=options)


@pytest.mark.parametrize("filtered", [False, True])
def test_drawing_cutouts_do_not_mutate_json_or_copper(tmp_path, filtered):
    job, raw = preview(filtered=filtered)
    original = step_to_json(job, raw)
    drawn = dielectric_drill_cutouts(raw)
    assert drawn.layer("SIGNAL_1") is raw.layer("SIGNAL_1")
    assert drawn.layer("DIELECTRIC_A").area_mm2 < 100
    assert len(drawn.layer("DIELECTRIC_A").geometry.region.holes) == 1
    assert not raw.layer("DIELECTRIC_A").geometry.region.holes
    groups = {g.get("data-name"): g for g in layer_groups(parse(render_svg(raw)))}
    assert path_of(groups["SIGNAL_1"]) == path_of(layer_groups(parse(render_svg(raw, layers=[raw.layer("SIGNAL_1")])))[0])
    assert float(groups["DIELECTRIC_A"].get("data-area")) == pytest.approx(drawn.layer("DIELECTRIC_A").area_mm2, abs=0.001)
    assert path_of(groups["DIELECTRIC_A"]).count("M") == 2
    assert path_of(groups["DIELECTRIC_A"]) in render_html(raw)
    write_outputs(raw, job, str(tmp_path), per_layer_svg=True)
    assert path_of(layer_groups(parse((tmp_path / "layers/DIELECTRIC_A.svg").read_text(encoding="utf-8")))[0]) == path_of(groups["DIELECTRIC_A"])
    json_path = next(tmp_path.glob("*.json"))
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["steps"][0]["layers"] == original["steps"][0]["layers"]
    assert step_to_json(job, raw)["steps"] == original["steps"]
    with pytest.raises(ValueError, match="drawing-only"):
        step_to_json(job, drawn)
    with pytest.raises(ValueError, match="drawing-only"):
        layer_to_json(drawn.layer("DIELECTRIC_A"))
