"""Native file-boundary acceptance: contract, geometry, materials and Python-free execution."""
import json

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

pytest.importorskip("pcbeq_rust")
from pcbeq_rust.native import run
from pcbeq.materialmap import read_document, iter_cells
from test_equiv import build_document, drill_document, rect, hole


def execute(document, tmp_path, **options):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document, allow_nan=True), encoding="utf-8")
    output = tmp_path / "native"
    summary = run(source, output, px=103, py=97, max_cell_mm=[3, 3], **options)
    return summary, read_document(output / "materialmap.json"), output


def test_full_pipeline_never_calls_python_input_geometry_materials_or_writer(tmp_path, monkeypatch):
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])

    def forbidden(*args, **kwargs):
        raise AssertionError("Python computation entered the native pipeline")

    for target in ("pcbeq.intermediate.read_document", "pcbeq.intermediate.BoardDocument.model_validate_json",
                   "pcbeq.equiv.equivalence_from_document", "pcbeq.equiv.plan_geometry",
                   "pcbeq.materialmap.MaterialMapWriter", "pcbeq.materials.MaterialBook"):
        monkeypatch.setattr(target, forbidden)
    summary, delivered, output = execute(document, tmp_path)
    assert summary["execution"] == "native-json-to-materialmap"
    assert summary["cells"] == 16
    audit = json.loads((output / "diagnostics/run.json").read_text(encoding="utf-8"))
    assert audit["grid"]["region_count"] == len(audit["grid"]["regions"])
    assert all(values == (385, 385, 385, 8960, 385) for _, _, values in iter_cells(delivered))
    assert not list(output.rglob("*.png"))


def test_native_anisotropy_and_material_precedence(tmp_path):
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 5)]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "native"
    summary = run(source, output, px=40, py=40, max_cell_mm=[10], materials={
        "global": {"copper": {"k_w_mk": 390}},
        "layer_types": {"copper": {"copper": {"k_w_mk": 395}}},
        "layers": {"CU": {"copper": {"k_w_mk": 400, "density_kg_m3": 8000, "specific_heat_j_kgk": 400},
                          "base": {"k_w_mk": 1, "density_kg_m3": 2000, "specific_heat_j_kgk": 1000}}}})
    assert summary["cells"] == 1
    values = read_document(output / "materialmap.json").regions[0].values[0]
    assert values == pytest.approx((200.5, round(2 / (1 / 400 + 1), 6), 200.5, 5000, 520))


def test_drills_only_affect_dielectrics_and_match_python_properties(tmp_path):
    from pcbeq import numerical
    from pcbeq.equiv import EquivalenceOptions, equivalence_from_document
    from pcbeq.grid import GridOptions
    document = drill_document(layers=[
        {"name": "CU", "polygons": [rect(0, 0, 10, 10), hole(2, 2, 8, 8), rect(4, 4, 6, 6)]},
        {"name": "FR", "type": "DIELECTRIC", "polygons": []},
        {"name": "D1", "type": "DRILL", "polygons": [rect(2, 2, 6, 4)]},
        {"name": "D2", "type": "DRILL", "polygons": [rect(4, 3, 7, 5)]}])
    _, delivered, _ = execute(document, tmp_path)
    with numerical.use(None):
        expected = equivalence_from_document(document, EquivalenceOptions(GridOptions(103, 97, 3)))
    reference = {(c.layer_name, c.x0, c.y0, c.x1, c.y1): c.property_values() for c in expected.cells}
    for region, bounds, values in iter_cells(delivered):
        assert values == pytest.approx(reference[(region.layer.name, *bounds)], rel=1e-12)
    assert sum(len(r.values) for r in delivered.regions) == len(reference)


def test_rigidflex_holes_and_layer_existence_are_preserved(tmp_path):
    from pcbeq.equiv import _region_from_polygons
    from pcbeq.rectify import profile_shape
    domains = [{"id": "step_profile", "source": "step_profile", "polygons": [rect(0, 0, 10, 10), hole(4, 4, 6, 6)]},
               {"id": "left", "source": "layer_profile", "polygons": [rect(0, 0, 3, 10)]}]
    document = build_document(domains=domains, layers=[
        {"name": "ALL", "polygons": []}, {"name": "LEFT", "domain_id": "left", "polygons": []}])
    _, delivered, _ = execute(document, tmp_path)
    for name, domain in (("ALL", domains[0]), ("LEFT", domains[1])):
        original = profile_shape(_region_from_polygons(domain["polygons"]))
        modeled = unary_union([box(*bounds) for region, bounds, _ in iter_cells(delivered) if region.layer.name == name])
        assert modeled.difference(original).area < 1e-8
        assert original.difference(modeled).area <= original.area * .05 + 1e-8
    assert not any(box(*bounds).intersects(box(4.01, 4.01, 5.99, 5.99)) for _, bounds, _ in iter_cells(delivered))


@pytest.mark.parametrize("change", ["units", "coordinate", "unknown", "domain", "z", "span"])
def test_invalid_input_is_rejected_before_publishing(tmp_path, change):
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    layer = document["steps"][0]["layers"][0]
    if change == "units":
        document["units"] = "inch"
    elif change == "coordinate":
        layer["polygons"][0]["points"][0][0] = "1.0"
    elif change == "unknown":
        document["unexpected"] = 1
    elif change == "domain":
        layer["domain_id"] = "missing"
    elif change == "z":
        layer["z_mm"]["end_mm"] = -1
    else:
        layer["copper_span"] = [0, 1]
    with pytest.raises(ValueError):
        execute(document, tmp_path)
    assert not (tmp_path / "native/materialmap.json").exists()


def test_sampling_failure_keeps_previous_materialmap(tmp_path):
    document = build_document(layers=[{"name": "CU", "polygons": []}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "native"
    run(source, output, px=20, py=20, max_cell_mm=[5])
    previous = read_document(output / "materialmap.json")
    with pytest.raises(ValueError, match="insufficient raster"):
        run(source, output, px=1, py=1, max_cell_mm=[1])
    assert read_document(output / "materialmap.json") == previous
    assert not list(output.glob("*.tmp"))


def test_python_images_need_only_materialmap(tmp_path):
    from pcbeq.map_images import render
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    _, _, output = execute(document, tmp_path)
    (tmp_path / "input.json").unlink()
    (output / "diagnostics/run.json").unlink()
    render(output / "materialmap.json", output, panel_width=480)
    assert (output / "regions/regions.png").is_file()
    assert (output / "grids/cartesian-grid.png").is_file()
    assert list((output / "heatmaps").glob("*.png"))
    assert (output / "index.html").is_file()


def test_rust_benchmark_uses_native_file_pipeline(tmp_path, monkeypatch):
    from tools import benchmark_pcbeq
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Python benchmark measure must not run for Rust")

    monkeypatch.setattr(benchmark_pcbeq, "measure", forbidden)
    output = tmp_path / "benchmark"
    assert benchmark_pcbeq.main([str(source), "--output", str(output), "--backend", "rust",
                                 "--px", "32", "--py", "32", "--max-cell-mm", "5",
                                 "--repeat", "1", "--warmup", "0"]) == 0
    report = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
    assert report["runs"][0]["execution"] == "native-json-to-materialmap"


def test_native_publication_invalidates_owned_images_and_keeps_user_files(tmp_path):
    from pcbeq.map_images import render
    document = build_document(layers=[{"name": "CU", "polygons": []}])
    _, _, output = execute(document, tmp_path)
    render(output / "materialmap.json", output, heatmap=False, grids=False)
    notes = output / "notes.txt"
    notes.write_text("user notes", encoding="utf-8")
    run(tmp_path / "input.json", output, px=32, py=32, max_cell_mm=[5])
    assert read_document(output / "materialmap.json").regions
    assert not (output / "index.html").exists()
    assert not (output / "regions/regions.png").exists()
    assert notes.read_text(encoding="utf-8") == "user notes"


@pytest.mark.parametrize("points,retreat", [
    ([(0, 0), (12, 0), (12, 12)], .3),
    ([(0, 0), (4, 0), (4, 1.9), (8, 1.9), (8, 0), (12, 0), (12, 4),
      (8, 4), (8, 2.1), (4, 2.1), (4, 4), (0, 4)], 3),
    ([(0, 0), (20, 0), (20, 20), (10.2, 20), (10, 23), (9.8, 20), (0, 20)], .2),
])
def test_native_contours_preserve_slopes_necks_and_local_spikes(tmp_path, points, retreat):
    from shapely.geometry import Polygon
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(profile={"island": True, "points": points},
                                                 layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")
    output = tmp_path / "native"
    run(source, output, max_cell_mm=[5], max_boundary_retreat_mm=retreat)
    delivered = read_document(output / "materialmap.json")
    modeled = unary_union([box(*bounds) for _, bounds, _ in iter_cells(delivered)])
    original = Polygon(points)
    assert modeled.difference(original).area < 1e-8
    assert original.difference(modeled).area <= original.area * .05 + 1e-8
    assert modeled.geom_type == "Polygon"
    assert original.boundary.buffer(retreat, quad_segs=128).covers(modeled.boundary)
    assert modeled.boundary.buffer(retreat, quad_segs=128).covers(original.boundary)
