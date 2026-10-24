"""Dense region order is the file contract; malformed rows cannot be skipped."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from pcbeq.equiv import write_materialmap
from pcbeq.materialmap import MaterialMapDocument, MaterialMapWriter, read_document, iter_cells
from pcbeq.visualize import read_materialmap, VisualizeError
from test_equiv import build_document, rect, run


def example(tmp_path, *, known=True):
    doc = build_document(layers=[{"name": "CU", "polygons": [rect(0,0,3,10)],
        "z_mm": {"start_mm": 1.0, "end_mm": 1.035, "thickness_known": known}}])
    result = run(doc, px=20, py=20, max_cell_mm=3)
    path = tmp_path / "board.MaterialMap.json"
    write_materialmap(result, str(path))
    return result, path, json.loads(path.read_text(encoding="utf-8"))


def test_known_and_unknown_z_are_not_confused_with_zero(tmp_path):
    _, path, data = example(tmp_path, known=False)
    assert data["regions"][0]["bounds_mm"]["z"] == [None, None]
    series = read_materialmap(str(path)).series[0]
    assert series.z_start_mm is None and series.z_end_mm is None
    assert not series.thickness_known
    result, path, data = example(tmp_path)
    assert data["regions"][0]["bounds_mm"]["z"] == [1.0, 1.035]
    result.cells = [replace(c, z_start_mm=0.0) for c in result.cells]
    write_materialmap(result, str(path))
    assert read_document(path).regions[0].bounds_mm.z == (0, 1.035)


def test_region_payload_contains_no_indices_coordinates_per_cell_or_sampling_stats(tmp_path):
    _, _, data = example(tmp_path)
    assert set(data) == {"units", "properties", "drill_model", "drill_scope", "regions"}
    for region in data["regions"]:
        assert set(region) == {"layer", "bounds_mm", "x_lines_mm", "y_lines_mm", "values"}
        assert len(region["bounds_mm"]) == 3
        assert all(len(v) == 5 and all(isinstance(x, (int,float)) for x in v) for v in region["values"])


def test_interleaved_nonuniform_regions_are_written_y_major_x_fast(tmp_path):
    doc = build_document(domains=[
        {"id":"step_profile","source":"step_profile","polygons":[rect(0,0,10,10)]},
        {"id":"left","source":"layer_profile","polygons":[rect(0,0,3,10)]}], layers=[
        {"name":"ALL","polygons":[rect(1,1,6,7)]},
        {"name":"LEFT","domain_id":"left","polygons":[]}])
    result = run(doc, px=40, py=40, max_cell_mm=3)
    path = tmp_path / "nonuniform.json"
    with MaterialMapWriter(path) as writer:
        for name in ("ALL", "LEFT"):
            # Deliberately reverse band and region order within a layer.
            for cell in reversed([c for c in result.cells if c.layer_name == name]):
                writer.add(cell)
                assert all(c.layer_name == name for cells in writer.pending.values() for c in cells)
        writer.finish(result)
    restored = read_document(path)
    assert len(restored.regions) > 2
    expected = {(c.layer_name,c.x0,c.y0,c.x1,c.y1):tuple(c.property_values()) for c in result.cells}
    for region, bounds, values in iter_cells(restored):
        assert expected.pop((region.layer.name,*bounds)) == values
    assert not expected
    assert len({round(b-a,8) for r in restored.regions for a,b in zip(r.x_lines_mm,r.x_lines_mm[1:])}) > 1


@pytest.mark.parametrize("bad", [[], [1,2,3,4], [1]*6, [0,1,1,1,1],
    [-1,1,1,1,1], [float("nan"),1,1,1,1], [True,1,1,1,1], ["1",1,1,1,1]])
def test_invalid_property_tuple_is_fatal_instead_of_shifting_rows(tmp_path, bad):
    _, path, data = example(tmp_path)
    data["regions"][0]["values"][0] = bad
    path.write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(VisualizeError, match="values"):
        read_materialmap(str(path))


@pytest.mark.parametrize("failure", ["missing_row","extra_row","descending_axis","bounds","overlap","extra_field","global_axis"])
def test_corrupt_geometry_and_dense_shape_are_rejected(tmp_path, failure):
    _, _, data = example(tmp_path)
    r = data["regions"][0]
    if failure == "missing_row": r["values"].pop()
    if failure == "extra_row": r["values"].append(r["values"][0])
    if failure == "descending_axis": r["x_lines_mm"].reverse()
    if failure == "bounds": r["bounds_mm"]["x"][1] += 1
    if failure == "overlap": data["regions"].append(deepcopy(r))
    if failure == "extra_field": r["cell_i"] = 0
    if failure == "global_axis":
        other=deepcopy(r); other["layer"]["name"]="OTHER"
        # A global line passing through CU must also occur in CU's axes.
        a,b=other["x_lines_mm"][:2]; other["x_lines_mm"].insert(1,(a+b)/2)
        other["values"]=[other["values"][0]]*((len(other["x_lines_mm"])-1)*(len(other["y_lines_mm"])-1))
        data["regions"].append(other)
    with pytest.raises(ValidationError):
        MaterialMapDocument.model_validate(data)


@pytest.mark.parametrize("failure", ["missing_cell","duplicate_cell","fatal"])
def test_unfinished_result_never_replaces_a_valid_file(tmp_path, failure):
    result,path,_=example(tmp_path)
    original=path.read_bytes()
    if failure == "missing_cell": result.cells.pop()
    if failure == "duplicate_cell": result.cells.append(result.cells[0])
    if failure == "fatal": result.fatal=True
    with pytest.raises(ValueError): write_materialmap(result,str(path))
    assert path.read_bytes()==original and not path.with_suffix(path.suffix+".tmp").exists()
