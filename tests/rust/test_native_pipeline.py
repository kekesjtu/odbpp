"""Native file-boundary acceptance: contract, geometry, materials and Python-free execution."""
import json
import sys

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

pytest.importorskip("pcbeq_rust")
from pcbeq_rust.native import run
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
    assert summary["execution"]=="native-json-to-materialmap"
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
    domains = [{"id": "step_profile", "source": "step_profile", "polygons": [rect(0, 0, 10, 10), hole(4, 4, 6, 6)]},
               {"id": "left", "source": "layer_profile", "polygons": [rect(0, 0, 3, 10)]}]
    document = build_document(domains=domains, layers=[
        {"name": "ALL", "polygons": []}, {"name": "LEFT", "domain_id": "left", "polygons": []}])
    _, delivered, _ = execute(document, tmp_path)
    for name, domain in (("ALL", domains[0]), ("LEFT", domains[1])):
        original = Polygon(domain["polygons"][0]["points"], [p["points"] for p in domain["polygons"][1:]])
        modeled = unary_union([box(*bounds) for region, bounds, _ in iter_cells(delivered) if region.layer.name == name])
        assert original.symmetric_difference(modeled).area <= original.area * .05 + 1e-8
    assert not any(box(*bounds).intersects(box(4.01, 4.01, 5.99, 5.99)) for _, bounds, _ in iter_cells(delivered))


def test_native_thin_region_uses_a_local_raster(tmp_path):
    document=build_document(domains=[{"id":"step_profile","source":"step_profile",
        "polygons":[rect(0,0,10,10),rect(11,4,12,4.0001)]}],
        layers=[{"name":"CU","polygons":[rect(0,0,6,10)]}])
    source=tmp_path/"input.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    output=tmp_path/"native"
    summary=run(source,output,grid_level="standard")
    delivered=read_document(output/"materialmap.json")
    assert summary["raster_sampling"]["layout"]=="region-local"
    assert summary["raster_sampling"]["total_pixels"]<20_000_000
    thin=next(r for r in delivered.regions if r.bounds_mm.y[1]-r.bounds_mm.y[0]<.001)
    assert thin.values and all(v==(.3,.3,.3,1850,1100) for v in thin.values)
    assert summary["cells"]==sum(len(r.values) for r in delivered.regions)
    assert len(delivered.regions)==2


def test_local_sampling_keeps_enclosing_holes_and_nested_islands(tmp_path):
    document=build_document(domains=[
        {"id":"step_profile","source":"step_profile","polygons":[rect(0,0,12,10)]},
        {"id":"centre","source":"layer_profile","polygons":[rect(4,4,6,6)]}],
        layers=[{"name":"CU","domain_id":"centre","polygons":[
            rect(0,0,10,10),hole(2,2,8,8),rect(4.5,4.5,5.5,5.5),rect(11,0,12,1)]}])
    source=tmp_path/"input.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    run(source,tmp_path/"native",px=24,py=20,max_cell_mm=[3])
    values=read_document(tmp_path/"native/materialmap.json").regions[0].values[0]
    harmonic=1/(.5/385+.5/.3)
    density=.25*8960+.75*1850
    cp=(.25*8960*385+.75*1850*1100)/density
    assert values==pytest.approx(((.3+harmonic)/2,)*2+(96.475,density,cp),abs=1e-4)


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
    from pcbeq_rust.map_images import render
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

    monkeypatch.setitem(sys.modules, "pcbeq", None)
    output = tmp_path / "benchmark"
    assert benchmark_pcbeq.main([str(source), "--output", str(output), "--backend", "rust",
                                 "--px", "32", "--py", "32", "--max-cell-mm", "5",
                                 "--repeat", "1", "--warmup", "0"]) == 0
    report = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
    assert report["runs"][0]["execution"] == "native-json-to-materialmap"


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


@pytest.mark.parametrize("points,ratio", [
    ([(0, 0), (12, 0), (12, 12)], .01),
    ([(0, 0), (4, 0), (4, 1.9), (8, 1.9), (8, 0), (12, 0), (12, 4),
      (8, 4), (8, 2.1), (4, 2.1), (4, 4), (0, 4)], .05),
    ([(0, 0), (20, 0), (20, 20), (10.2, 20), (10, 23), (9.8, 20), (0, 20)], .05),
])
def test_native_area_budget_preserves_slopes_and_connections(tmp_path, points, ratio):
    from shapely.geometry import Polygon
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(profile={"island": True, "points": points},
                                                 layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")
    output = tmp_path / "native"
    run(source, output, max_cell_mm=[5], max_area_error_ratio=ratio)
    delivered = read_document(output / "materialmap.json")
    modeled = unary_union([box(*bounds) for _, bounds, _ in iter_cells(delivered)])
    original = Polygon(points)
    assert original.symmetric_difference(modeled).area <= original.area * ratio + 1e-8
    assert modeled.geom_type == "Polygon"


def test_equal_effective_profiles_share_one_plan_despite_vertex_order_and_extent(tmp_path):
    duplicate = rect(-1, -1, 11, 11)
    duplicate["points"].reverse()
    document = build_document(domains=[
        {"id": "step_profile", "source": "step_profile", "polygons": [{"island": True,
         "points": [(0, 0), (10, 0), (10, 5), (6, 5), (6, 10), (0, 10)]}]},
        {"id": "duplicate", "source": "layer_profile", "polygons": [duplicate]},
    ], layers=[{"name": "A", "polygons": []},
               {"name": "B", "domain_id": "duplicate", "polygons": []}])
    _, delivered, output = execute(document, tmp_path)
    report = json.loads((output / "diagnostics/run.json").read_text())["rectification"]
    assert report["unique_profile_count"] == 1
    assert report["reused_profile_count"] == 1
    assert report["rectangle_count"] == 2
    ids = report["audit"]["domain_region_ids"]
    assert ids["step_profile"] == ids["duplicate"]
    by_layer = {name: [(r.bounds_mm, r.grid_counts) for r in delivered.regions if r.layer.name == name]
                for name in ("A", "B")}
    assert by_layer["A"] == by_layer["B"]


def test_distinct_profiles_do_not_split_each_others_regions(tmp_path):
    document = build_document(domains=[
        {"id": "step_profile", "source": "step_profile", "polygons": [rect(0, 0, 10, 10)]},
        {"id": "left", "source": "layer_profile", "polygons": [rect(0, 0, 3, 10)]},
    ], layers=[{"name": "ALL", "polygons": []},
               {"name": "LEFT", "domain_id": "left", "polygons": []}])
    _, delivered, output = execute(document, tmp_path)
    regions = {r.layer.name: r for r in delivered.regions}
    assert len(delivered.regions) == 2
    assert regions["ALL"].bounds_mm.x == (0, 10)
    assert regions["LEFT"].bounds_mm.x == (0, 3)
    report = json.loads((output / "diagnostics/run.json").read_text())["rectification"]
    assert report["unique_profile_count"] == 2
    assert report["audit"]["cross_layer_alignment_required"] is False


def test_fast_outside_pixels_are_base_even_when_copper_covers_them(tmp_path):
    points = [(2, 0), (10, 0), (10, 10), (0, 10), (0, 2)]
    document = build_document(profile={"island": True, "points": points},
                              layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document))
    output = tmp_path / "native"
    run(source, output, px=100, py=100, max_cell_mm=[10])
    delivered = read_document(output / "materialmap.json")
    assert len(delivered.regions) == 1
    report = json.loads((output / "diagnostics/run.json").read_text())
    layer = report["layers"][0]
    assert layer["outside_material"] == "base"
    assert layer["outside_base_pixels"] > 0
    phi = 1 - layer["outside_base_pixels"] / 10000
    values = delivered.regions[0].values[0]
    assert values[2] == pytest.approx(phi * 385 + (1 - phi) * .3, abs=1e-6)
    assert values[3] == pytest.approx(phi * 8960 + (1 - phi) * 1850, abs=1e-4)
    geometry = report["rectification"]["audit"]["domains"]["step_profile"]
    assert geometry["lost_area_mm2"] == pytest.approx(0)
    assert geometry["gained_area_mm2"] == pytest.approx(2)
    assert geometry["relative_area_error"] == pytest.approx(2 / 98)


def test_profile_hole_witness_does_not_remove_its_nested_island(tmp_path):
    document = build_document(domains=[{
        "id": "step_profile", "source": "step_profile",
        "polygons": [rect(0, 0, 10, 10), hole(2, 2, 8, 8), rect(4, 4, 6, 6)],
    }], layers=[{"name": "CU", "polygons": []}])
    _, delivered, _ = execute(document, tmp_path)
    model = unary_union([box(*bounds) for _, bounds, _ in iter_cells(delivered)])
    assert len(model.geoms) == 2
    assert model.covers(box(4, 4, 6, 6))
    assert sum(len(p.interiors) for p in model.geoms) == 1


def test_fast_planning_is_independent_of_material_sampling_density(tmp_path):
    profile = {"island": True, "points": [(0, 0), (12, 0), (12, 12)]}
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(profile=profile, layers=[{"name": "CU", "polygons": []}])))
    layouts = []
    for density in (160, 320):
        output = tmp_path / str(density)
        run(source, output, px=density, py=density, max_cell_mm=[5])
        layouts.append([(r.bounds_mm, r.grid_counts) for r in read_document(output / "materialmap.json").regions])
    assert layouts[0] == layouts[1]
