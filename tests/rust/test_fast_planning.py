"""Feature-preserving FAST geometry and independent effective-profile planning."""
import json
import pytest
from shapely.geometry import box
from shapely.ops import unary_union
from pcbeq_rust.runner import run
from pcbeq_rust.materialmap import read_document, iter_cells
from board_fixture import build_document, rect, hole


def execute(document, tmp_path, **options):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "native"
    summary = run(source, output, max_cell_mm=[3, 3], **options)
    return summary, read_document(output / "materialmap.json"), output


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


def test_equal_effective_profiles_share_one_plan_despite_vertex_order_and_collinear_points(tmp_path):
    duplicate = {"island": True,
                 "points": [(6, 10), (6, 5), (10, 5), (10, 0), (5, 0), (0, 0), (0, 10)]}
    document = build_document(profiles=[
        {"id": "step_profile", "polygons": [{"island": True,
         "points": [(0, 0), (10, 0), (10, 5), (6, 5), (6, 10), (0, 10)]}]},
        {"id": "duplicate", "polygons": [duplicate]},
    ], layers=[{"name": "A", "polygons": []},
               {"name": "B", "profile_id": "duplicate", "polygons": []}])
    _, delivered, output = execute(document, tmp_path)
    report = json.loads((output / "diagnostics.json").read_text())["geometry"]
    assert len(report["profiles"]) == 1
    profile = next(iter(report["profiles"].values()))
    assert report["profile_count"] == 1
    assert profile["layers"] == ["A", "B"]
    assert profile["layer_count"] == 2
    assert profile["region_count"] == 2
    assert profile["cell_count"] == sum(len(r.values) for r in delivered.regions if r.layer.name == "A")
    by_layer = {name: [(r.bounds_mm, r.grid_counts) for r in delivered.regions if r.layer.name == name]
                for name in ("A", "B")}
    assert by_layer["A"] == by_layer["B"]


def test_distinct_profiles_do_not_split_each_others_regions(tmp_path):
    document = build_document(profiles=[
        {"id": "step_profile", "polygons": [rect(0, 0, 10, 10)]},
        {"id": "left", "polygons": [rect(0, 0, 3, 10)]},
    ], layers=[{"name": "ALL", "polygons": []},
               {"name": "LEFT", "profile_id": "left", "polygons": []}])
    _, delivered, output = execute(document, tmp_path)
    regions = {r.layer.name: r for r in delivered.regions}
    assert len(delivered.regions) == 2
    assert regions["ALL"].bounds_mm.x == (0, 10)
    assert regions["LEFT"].bounds_mm.x == (0, 3)
    report = json.loads((output / "diagnostics.json").read_text())["geometry"]
    assert len(report["profiles"]) == 2


def test_profiles_differing_only_outside_step_are_not_reused(tmp_path):
    document = build_document(profiles=[
        {"id": "step_profile", "polygons": [rect(0, 0, 10, 10)]},
        {"id": "extended", "polygons": [rect(-1, -1, 11, 11)]},
    ], layers=[{"name": "BOARD"}, {"name": "EXTENDED", "profile_id": "extended"}])
    _, delivered, output = execute(document, tmp_path)
    regions = {r.layer.name: r for r in delivered.regions}
    assert regions["BOARD"].bounds_mm.x == (0, 10)
    assert regions["EXTENDED"].bounds_mm.x == (-1, 11)
    report = json.loads((output / "diagnostics.json").read_text())
    assert report["geometry"]["profile_count"] == 2
    assert sorted(p["area"]["original_mm2"] for p in report["accuracy"]["profiles"].values()) == [100, 144]


def test_fast_outside_pixels_are_base_even_when_copper_covers_them(tmp_path):
    points = [(.2, 0), (10, 0), (10, 10), (0, 10), (0, .2)]
    document = build_document(profile={"island": True, "points": points},
                              layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document))
    output = tmp_path / "native"
    run(source, output, pixels_per_cell_axis=100, max_cell_mm=[10])
    delivered = read_document(output / "materialmap.json")
    assert len(delivered.regions) == 1
    report = json.loads((output / "diagnostics.json").read_text())
    # Only (0.05, 0.05) lies strictly outside; the two boundary centers belong to the profile.
    phi = 1 - 1 / 10000
    values = delivered.regions[0].values[0]
    assert values[2] == pytest.approx(phi * 385 + (1 - phi) * .3, abs=1e-6)
    assert values[3] == pytest.approx(phi * 8960 + (1 - phi) * 1850, abs=1e-4)
    area = report["accuracy"]["profiles"]["step_profile"]["area"]
    assert area["lost_mm2"] == pytest.approx(0)
    assert area["gained_mm2"] == pytest.approx(.02)
    assert area["relative_error"] == pytest.approx(.02 / 99.98)


def test_profile_hole_witness_does_not_remove_its_nested_island(tmp_path):
    document = build_document(profiles=[{
        "id": "step_profile", "polygons": [rect(0, 0, 10, 10), hole(2, 2, 8, 8), rect(4, 4, 6, 6)],
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
    for density in (1, 16):
        output = tmp_path / str(density)
        run(source, output, pixels_per_cell_axis=density, max_cell_mm=[5])
        layouts.append([(r.bounds_mm, r.grid_counts) for r in read_document(output / "materialmap.json").regions])
    assert layouts[0] == layouts[1]
