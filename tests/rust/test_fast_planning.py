"""Original FAST geometry acceptance and independent effective-profile planning."""
import json
import pytest
from shapely.geometry import box
from shapely.ops import unary_union
from pcbeq_rust.native import run
from pcbeq_rust.materialmap import read_document, iter_cells
from board_fixture import build_document, rect, hole


def execute(document, tmp_path, **options):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "native"
    summary = run(source, output, px=103, py=97, max_cell_mm=[3, 3], **options)
    return summary, read_document(output / "materialmap.json"), output


@pytest.mark.parametrize("points,ratio", [
    ([(0, 0), (12, 0), (12, 12)], .01),
    ([(0, 0), (4, 0), (4, 1.9), (8, 1.9), (8, 0), (12, 0), (12, 4),
      (8, 4), (8, 2.1), (4, 2.1), (4, 4), (0, 4)], .05),
    ([(0, 0), (20, 0), (20, 20), (10.2, 20), (10, 23), (9.8, 20), (0, 20)], .05),
])
def test_paper_fast_raster_budget_and_vector_diagnostics(tmp_path, points, ratio):
    from shapely.geometry import Polygon
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(profile={"island": True, "points": points},
                                                 layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")
    output = tmp_path / "native"
    run(source, output, max_cell_mm=[5], max_area_error_ratio=ratio)
    delivered = read_document(output / "materialmap.json")
    modeled = unary_union([box(*bounds) for _, bounds, _ in iter_cells(delivered)])
    original = Polygon(points)
    report = json.loads((output / "diagnostics/run.json").read_text())["rectification"]["audit"]["domains"]["step_profile"]
    assert report["audit"]["search"]["relative_area_error"] <= ratio
    assert report["relative_area_error"] == pytest.approx(original.symmetric_difference(modeled).area / original.area)
    assert report["audit"]["topology_is_constraint"] is False


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
    points = [(.2, 0), (10, 0), (10, 10), (0, 10), (0, .2)]
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
    assert geometry["gained_area_mm2"] == pytest.approx(.02)
    assert geometry["relative_area_error"] == pytest.approx(.02 / 99.98)


def test_paper_fast_reports_nested_profile_topology_without_modifying_selection(tmp_path):
    document = build_document(domains=[{
        "id": "step_profile", "source": "step_profile",
        "polygons": [rect(0, 0, 10, 10), hole(2, 2, 8, 8), rect(4, 4, 6, 6)],
    }], layers=[{"name": "CU", "polygons": []}])
    _, delivered, output = execute(document, tmp_path)
    model = unary_union([box(*bounds) for _, bounds, _ in iter_cells(delivered)])
    report=json.loads((output / "diagnostics/run.json").read_text())["rectification"]["audit"]["domains"]["step_profile"]
    parts=[model] if model.geom_type=="Polygon" else list(model.geoms)
    assert report["audit"]["topology"]["model_components"]==len(parts)
    assert report["audit"]["topology"]["model_holes"]==sum(len(p.interiors) for p in parts)


def test_fast_planning_is_independent_of_material_sampling_density(tmp_path):
    profile = {"island": True, "points": [(0, 0), (12, 0), (12, 12)]}
    source = tmp_path / "input.json"
    source.write_text(json.dumps(build_document(profile=profile, layers=[{"name": "CU", "polygons": []}])))
    layouts = []
    for density in (2048, 4096):
        output = tmp_path / str(density)
        run(source, output, px=density, py=density, max_cell_mm=[5])
        layouts.append([(r.bounds_mm, r.grid_counts) for r in read_document(output / "materialmap.json").regions])
    assert layouts[0] == layouts[1]
