"""ODB++ layer extents inherit step holes and may extend beyond step islands."""
import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from board_fixture import build_document, drill_document, hole, rect
from conftest import write_tree
from pcbkit_rust.export import PreviewOptions, build_step_preview, step_to_json
from pcbkit_rust.geometry import TessellationOptions
from pcbkit_rust.job import load_job
from pcbkit_rust.svg import render_svg
from pcbeq_rust.materialmap import iter_cells, read_document
from pcbeq_rust.runner import run
from tools.audit_out import audit_outputs, physical_shape


def profile_text(polygons):
    lines = ["UNITS=MM", "F 1", "S P 0"]
    for polygon in polygons:
        points = list(reversed(polygon["points"])) if polygon["island"] else polygon["points"]
        start, *rest = points
        lines.append(f"OB {start[0]} {start[1]} {'I' if polygon['island'] else 'H'}")
        lines.extend(f"OS {x} {y}" for x, y in [*rest, start])
        lines.append("OE")
    return "\n".join([*lines, "SE", ""])


def profile_job(tmp_path, step_polygons, layer_polygons):
    files = {
        "misc/info": "JOB_NAME=profiles\nODB_VERSION_MAJOR=8\nODB_VERSION_MINOR=1\nUNITS=MM\n",
        "matrix/matrix": "STEP {\nCOL=1\nNAME=board\n}\n" + "\n".join(
            f"LAYER {{\nROW={row}\nNAME={name}\nCONTEXT=BOARD\nTYPE={kind}\nPOLARITY={polarity}\nFORM=RIGID\n}}"
            for row, name, kind, polarity in [(1, "FR", "DIELECTRIC", "POSITIVE"),
                                             (2, "CU", "SIGNAL", "NEGATIVE")]),
        "steps/board/stephdr": "UNITS=MM\nX_DATUM=0\nY_DATUM=0\nX_ORIGIN=0\nY_ORIGIN=0\n",
        "steps/board/profile": profile_text(step_polygons),
    }
    for name, thickness in [("FR", ".layer_dielectric=0.1"), ("CU", ".copper_weight=35")]:
        prefix = f"steps/board/layers/{name}"
        files[prefix + "/features"] = "UNITS=MM\nF 0\n"
        files[prefix + "/attrlist"] = f"UNITS=MM\n{thickness}\n"
        if layer_polygons is not None:
            files[prefix + "/profile"] = profile_text(layer_polygons)
    return load_job(str(write_tree(tmp_path / "job", files)))


def svg_shape(data):
    polygons = []
    for ring in re.findall(r"M([^Z]+)Z", data):
        points = [tuple(map(float, pair.split())) for pair in ring.split("L")]
        polygons.append({"island": not Polygon(points).exterior.is_ccw, "points": points})
    return physical_shape(polygons)


@pytest.mark.parametrize("layer_polygons", [
    [rect(-1, -1, 11, 11), hole(3, 3, 5, 5)],  # Overlapping layer and step holes.
    [rect(3, 1, 5, 5)],                       # Step hole intersects a layer edge.
    [rect(1, 2.5, 5, 3.5)],                  # Step hole splits an island.
    [rect(2.5, 2.5, 3.5, 3.5)],              # Step hole removes an entire island.
    [rect(12, 2, 14, 4)],                    # Layer island outside the step.
], ids=["overlapping-holes", "edge-notch", "split-island", "removed-island", "outside-step"])
def test_parser_resolves_holes_consistently_in_domains_materials_and_svg(tmp_path, layer_polygons):
    step_polygons = [rect(0, 0, 10, 10), hole(2, 2, 4, 4), hole(6, 2, 8, 4)]
    expected = physical_shape(layer_polygons).difference(
        unary_union([box(2, 2, 4, 4), box(6, 2, 8, 4)]))
    job = profile_job(tmp_path, step_polygons, layer_polygons)
    preview = build_step_preview(job)
    document = step_to_json(job, preview)
    step = document["steps"][0]
    domains = {d["id"]: physical_shape(d["polygons"]) for d in step["domains"]}
    assert domains[step["profile_domain_id"]].equals(physical_shape(step_polygons))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    svg = ElementTree.fromstring(render_svg(preview))
    outlines = svg.find(".//svg:g[@id='odb-domains']", ns)
    for layer in step["layers"]:
        assert layer["geometry_status"] == "ready"
        assert domains[layer["domain_id"]].equals(expected)
        # Both the dielectric sheet and negative copper start from this extent.
        assert physical_shape(layer["polygons"]).equals(expected)
        path = outlines.find(f"svg:path[@data-name='{layer['name']}']", ns)
        assert svg_shape(path.attrib["d"]).equals(expected)


def test_missing_layer_profile_defaults_to_complete_step_with_nested_island(tmp_path):
    polygons = [rect(0, 0, 10, 10), hole(2, 2, 8, 8), rect(4, 4, 6, 6)]
    job = profile_job(tmp_path, polygons, None)
    step = step_to_json(job, build_step_preview(job))["steps"][0]
    expected = physical_shape(polygons)
    assert len(step["domains"]) == 1
    for layer in step["layers"]:
        assert layer["domain_id"] == step["profile_domain_id"]
        assert physical_shape(layer["polygons"]).equals(expected)


def test_arc_holes_use_the_same_tessellation_in_materials_domains_and_svg(tmp_path):
    job = profile_job(tmp_path, [rect(0, 0, 10, 10)], [rect(-1, -1, 11, 11)])
    path = Path(job.source_path) / "steps/board/profile"
    path.write_text(profile_text([rect(0, 0, 10, 10)]).replace(
        "SE\n", "OB 4 5 H\nOC 4 5 5 5 N\nOE\nSE\n"), encoding="utf-8")
    job = load_job(job.source_path)
    preview = build_step_preview(job, options=PreviewOptions(
        tessellation=TessellationOptions(sagitta_mm=.2)))
    step = step_to_json(job, preview)["steps"][0]
    domains = {d["id"]: physical_shape(d["polygons"]) for d in step["domains"]}
    step_hole = box(0, 0, 10, 10).difference(domains[step["profile_domain_id"]])
    assert 2 < step_hole.area < 3
    expected = box(-1, -1, 11, 11).difference(step_hole)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    outlines = ElementTree.fromstring(render_svg(preview)).find(".//svg:g[@id='odb-domains']", ns)
    for layer in step["layers"]:
        assert domains[layer["domain_id"]].equals(expected)
        assert physical_shape(layer["polygons"]).equals(expected)
        path = outlines.find(f"svg:path[@data-name='{layer['name']}']", ns)
        assert svg_shape(path.attrib["d"]).equals(expected)


def test_invalid_explicit_layer_profile_does_not_default_to_step(tmp_path):
    job = profile_job(tmp_path, [rect(0, 0, 10, 10)], [])
    step = step_to_json(job, build_step_preview(job))["steps"][0]
    for layer in step["layers"]:
        assert layer["geometry_status"] == "failed"
        assert layer.get("domain_id") is None
        assert layer["geometry_error"]


def test_parser_to_equivalence_keeps_holes_and_full_layer_extent(tmp_path):
    job = profile_job(tmp_path, [rect(0, 0, 10, 10), hole(4, 4, 6, 6)],
                      [rect(-1, -1, 11, 11), hole(5, 5, 7, 7)])
    document = step_to_json(job, build_step_preview(job))
    source = tmp_path / "case/rust/pcbkit/intermediate.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(document), encoding="utf-8")
    output = source.parent.parent / "pcbeq"
    run(source, output, px=100, py=100, max_cell_mm=[3], max_area_error_ratio=0)
    delivered = read_document(output / "materialmap.json")
    expected = box(-1, -1, 11, 11).difference(unary_union([box(4, 4, 6, 6), box(5, 5, 7, 7)]))
    for name, values in [("FR", (.3, .3, .3, 1850, 1100)), ("CU", (385, 385, 385, 8960, 385))]:
        cells = [(bounds, v) for r, bounds, v in iter_cells(delivered) if r.layer.name == name]
        assert unary_union([box(*bounds) for bounds, _ in cells]).equals(expected)
        assert all(v == values for _, v in cells)
    report = json.loads((output / "diagnostics.json").read_text())
    assert report["geometry"]["profile_count"] == 1
    profile, = report["geometry"]["profiles"].values()
    assert profile["layer_count"] == 2
    assert audit_outputs(tmp_path / "case")[0]["physical_regions"] == len(delivered.regions)


@pytest.mark.parametrize("density", [{}, {"px": 40, "py": 60}], ids=["automatic", "explicit"])
@pytest.mark.parametrize("bounds", [(-1, -1, 2, 2), (12, 12, 14, 14), (4, 4, 6, 6)],
                         ids=["cross-bounds", "outside-bounds", "outside-islands"])
def test_equivalence_preserves_layer_extent_and_copper_outside_step(tmp_path, density, bounds):
    # A concave step also exercises layer material outside its islands but inside its bbox.
    board = {"island": True, "points": [(0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)]}
    document = build_document(domains=[
        {"id": "step_profile", "source": "step_profile", "polygons": [board]},
        {"id": "layer", "source": "layer_profile", "polygons": [rect(*bounds)]},
    ], layers=[{"name": "CU", "domain_id": "layer", "polygons": [rect(*bounds)]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "native"
    run(source, output, max_cell_mm=[10], **density)
    delivered = read_document(output / "materialmap.json")
    assert unary_union([box(*b) for _, b, _ in iter_cells(delivered)]).equals(box(*bounds))
    assert all(v == (385, 385, 385, 8960, 385) for _, _, v in iter_cells(delivered))


def test_explicit_sampling_extends_board_pixel_spacing_and_phase(tmp_path):
    domain = rect(-.7, -.7, 1.3, 1.3)
    document = build_document(domains=[
        {"id": "step_profile", "source": "step_profile", "polygons": [rect(0, 0, 10, 10)]},
        {"id": "layer", "source": "layer_profile", "polygons": [domain]},
    ], layers=[{"name": "CU", "domain_id": "layer", "polygons": [rect(-.7, -.7, -.3, 1.3)]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    run(source, tmp_path / "out", px=10, py=20, max_cell_mm=[10])
    region, = read_document(tmp_path / "out/materialmap.json").regions
    assert region.bounds_mm.x == (-.7, 1.3)
    # Reference X centers are -0.5 and 0.5: exactly one column is copper.
    assert region.values[0][2] == pytest.approx((385 + .3) / 2)
    assert region.values[0][3] == pytest.approx((8960 + 1850) / 2)


def test_dielectric_drill_material_outside_step_is_sampled(tmp_path):
    outside = rect(12, -3, 14, -1)
    document = drill_document(domains=[
        {"id": "step_profile", "source": "step_profile", "polygons": [rect(0, 0, 10, 10)]},
        {"id": "outside", "source": "layer_profile", "polygons": [outside]},
    ], layers=[{"name": "FR", "type": "DIELECTRIC", "domain_id": "outside"},
               {"name": "DRILL", "type": "DRILL", "polygons": [outside]}])
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    run(source, tmp_path / "out", px=50, py=50, max_cell_mm=[10])
    regions = read_document(tmp_path / "out/materialmap.json").regions
    dielectric = [r for r in regions if r.layer.name == "FR"]
    assert len(dielectric) == 1
    assert dielectric[0].values[0] == (385, 385, 385, 8960, 385)
