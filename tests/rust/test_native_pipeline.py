"""Native file-boundary acceptance: contract, geometry, materials and Python-free execution."""
import json
import sys

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

pytest.importorskip("pcbeq_rust")
from pcbeq_rust.runner import run
from pcbeq_rust.materialmap import read_document, iter_cells
from board_fixture import build_document, drill_document, rect, hole


def execute(document, tmp_path, **options):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document, allow_nan=True), encoding="utf-8")
    output = tmp_path / "native"
    summary = run(source, output, px=103, py=97, max_cell_mm=[3, 3], **options)
    return summary, read_document(output / "materialmap.json"), output


def test_full_pipeline_has_no_python_product_imports(tmp_path):
    document = build_document(layers=[{"name":"CU","polygons":[rect(0,0,10,10)]}])
    summary,delivered,output=execute(document,tmp_path)
    assert summary["cells"]==16
    assert all(values==(385,385,385,8960,385) for _,_,values in iter_cells(delivered))
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


def test_drills_only_affect_dielectrics_and_combine_overlap_once(tmp_path):
    document=drill_document(layers=[
        {"name":"CU","polygons":[rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]},
        {"name":"FR","type":"DIELECTRIC","polygons":[]},
        {"name":"D1","type":"DRILL","polygons":[rect(2,2,6,4)]},
        {"name":"D2","type":"DRILL","polygons":[rect(4,3,7,5)]}])
    source=tmp_path/"input.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    run(source,tmp_path/"native",px=100,py=100,max_cell_mm=[10])
    delivered=read_document(tmp_path/"native/materialmap.json")
    values={r.layer.name:r.values[0] for r in delivered.regions}
    assert values["CU"][2]==pytest.approx(.68*385+.32*.3)
    assert values["FR"][2]==pytest.approx(.12*385+.88*.3)


def test_rigidflex_holes_and_layer_existence_are_preserved(tmp_path):
    from shapely.geometry import Polygon
    profiles = [{"id": "step_profile", "polygons": [rect(0, 0, 10, 10), hole(4, 4, 6, 6)]},
               {"id": "left", "polygons": [rect(0, 0, 3, 10)]}]
    document = build_document(profiles=profiles, layers=[
        {"name": "ALL", "polygons": []}, {"name": "LEFT", "profile_id": "left", "polygons": []}])
    _, delivered, _ = execute(document, tmp_path)
    for name, profile in (("ALL", profiles[0]), ("LEFT", profiles[1])):
        original = Polygon(profile["polygons"][0]["points"], [p["points"] for p in profile["polygons"][1:]])
        modeled = unary_union([box(*bounds) for region, bounds, _ in iter_cells(delivered) if region.layer.name == name])
        assert original.symmetric_difference(modeled).area <= original.area * .05 + 1e-8
    assert not any(box(*bounds).intersects(box(4.01, 4.01, 5.99, 5.99)) for _, bounds, _ in iter_cells(delivered))


def test_native_thin_region_uses_a_local_raster(tmp_path):
    document=build_document(profiles=[{"id":"step_profile","polygons":[rect(0,0,10,10),rect(11,4,12,4.0001)]}],
        layers=[{"name":"CU","polygons":[rect(0,0,6,10)]}])
    source=tmp_path/"input.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    output=tmp_path/"native"
    summary=run(source,output,grid_level="standard")
    delivered=read_document(output/"materialmap.json")
    profile=json.loads((output/"diagnostics.json").read_text())["geometry"]["profiles"]["step_profile"]
    assert profile["region_size_mm"]["min"]["y"] == pytest.approx(.0001, abs=1e-8)
    assert profile["cell_size_mm"]["min"]["y"] == pytest.approx(.0001, abs=1e-8)
    thin=next(r for r in delivered.regions if r.bounds_mm.y[1]-r.bounds_mm.y[0]<.001)
    assert thin.values and all(v==(.3,.3,.3,1850,1100) for v in thin.values)
    assert summary["cells"]==sum(len(r.values) for r in delivered.regions)
    assert len(delivered.regions)==2


def test_local_sampling_keeps_enclosing_holes_and_nested_islands(tmp_path):
    document=build_document(profiles=[
        {"id":"step_profile","polygons":[rect(0,0,12,10)]},
        {"id":"centre","polygons":[rect(4,4,6,6)]}],
        layers=[{"name":"CU","profile_id":"centre","polygons":[
            rect(0,0,10,10),hole(2,2,8,8),rect(4.5,4.5,5.5,5.5),rect(11,0,12,1)]}])
    source=tmp_path/"input.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    run(source,tmp_path/"native",px=24,py=20,max_cell_mm=[3])
    values=read_document(tmp_path/"native/materialmap.json").regions[0].values[0]
    harmonic=1/(.5/385+.5/.3)
    density=.25*8960+.75*1850
    cp=(.25*8960*385+.75*1850*1100)/density
    assert values==pytest.approx(((.3+harmonic)/2,)*2+(96.475,density,cp),abs=1e-4)


@pytest.mark.parametrize("change", ["units", "coordinate", "unknown", "profile", "z", "endpoint"])
def test_invalid_input_is_rejected_before_publishing(tmp_path, change):
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    layer = document["steps"][0]["layers"][0]
    if change == "units":
        document["units"] = "inch"
    elif change == "coordinate":
        layer["polygons"][0]["points"][0][0] = "1.0"
    elif change == "unknown":
        document["unexpected"] = 1
    elif change == "profile":
        document["steps"][0]["profiles"][0]["layers"] = ["missing"]
    elif change == "z":
        layer["z_mm"]["end_mm"] = -1
    else:
        layer["start_layer"] = "missing"
    with pytest.raises(ValueError):
        execute(document, tmp_path)
    assert not (tmp_path / "native/materialmap.json").exists()
    report = json.loads((tmp_path / "native/diagnostics.json").read_text())
    assert report["issues"]["status"] == "failed"
    assert report["issues"]["errors"][-1]["blocking"]
    assert report["geometry"]["profile_count"] is None


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
    report = json.loads((output / "diagnostics.json").read_text())
    assert report["issues"]["status"] == "failed"
    assert report["issues"]["errors"][-1]["code"] == "mesh.failed"
    profile = report["geometry"]["profiles"]["step_profile"]
    assert profile["region_count"] == 1 and profile["cell_count"] is None
    assert report["performance"]["stages_s"]["sampling"] is None


def test_python_images_need_only_materialmap(tmp_path):
    from pcbeq_rust.map_images import render
    document = build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    _, _, output = execute(document, tmp_path)
    (tmp_path / "input.json").unlink()
    (output / "diagnostics.json").unlink()
    render(output / "materialmap.json", output, panel_width=480)
    assert (output / "regions/regions.png").is_file()
    assert (output / "grids/cartesian-grid.png").is_file()
    assert list((output / "heatmaps").glob("*.png"))
    assert (output / "index.html").is_file()
    assert not (output / "diagnostics").exists()


def test_rust_benchmark_uses_native_file_pipeline(tmp_path, monkeypatch):
    from tools import benchmark_pcbeq
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")

    monkeypatch.setitem(sys.modules, "pcbeq", None)
    output = tmp_path / "benchmark"
    assert benchmark_pcbeq.main([str(source), "--output", str(output), "--backend", "rust",
                                 "--px", "32", "--py", "32", "--max-cell-mm", "5",
                                 "--repeat", "1", "--warmup", "0"]) == 0
    report = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
    assert report["runs"][0]["cells"] == 4


def test_native_publication_invalidates_owned_images_and_keeps_user_files(tmp_path):
    from pcbeq_rust.map_images import render
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
