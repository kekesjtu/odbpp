"""Drill coverage depends on matrix rows, independently of thickness and Z."""
import json

import pytest

from board_fixture import build_document, rect
from pcbeq_rust.materialmap import read_document
from pcbeq_rust.runner import run
from pcbkit_rust.intermediate import BoardDocument


def topology_document():
    return build_document(layers=[
        {"name": "OUT_TOP", "type": "DIELECTRIC", "row": 10},
        {"name": "C0", "copper_index": 0, "row": 20},
        {"name": "A", "type": "DIELECTRIC", "row": 30},
        {"name": "C1", "copper_index": 1, "row": 40},
        {"name": "B", "type": "DIELECTRIC", "row": 50},
        {"name": "C2", "copper_index": 2, "row": 60},
        {"name": "OUT_BOTTOM", "type": "DIELECTRIC", "row": 70},
        {"name": "D01", "type": "DRILL", "row": 80,
         "copper_span": [0, 1], "polygons": [rect(0, 0, 5, 10)]},
        {"name": "D12", "type": "DRILL", "row": 90,
         "copper_span": [1, 2], "polygons": [rect(5, 0, 10, 10)]},
        {"name": "D02", "type": "DRILL", "row": 100,
         "copper_span": [0, 2], "polygons": [rect(4, 0, 6, 10)]},
    ])


@pytest.mark.parametrize("z_mode", ["unknown_zero", "known_zero", "disjoint", "overlapping"])
def test_native_drill_coverage_uses_rows_without_dielectric_spans(tmp_path, z_mode):
    document = topology_document()
    layers = document["steps"][0]["layers"]
    for layer in layers:
        if layer["type"] == "DRILL":
            continue
        low = layer["row"] if z_mode == "disjoint" else 0
        high = low + 1 if z_mode in ("disjoint", "overlapping") else low
        layer["z_mm"] = {"start_mm": low, "end_mm": high,
                         "thickness_known": z_mode != "unknown_zero"}
    # Array order is not physical order; row is authoritative.
    layers.reverse()
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    summary = run(source, tmp_path / "out", px=100, py=100, max_cell_mm=[10])
    report = json.loads((tmp_path / "out/diagnostics/run.json").read_text())
    overlays = {layer["name"]: set(layer["drill_overlays"])
                for layer in report["layers"] if layer["status"] == "equivalent"}
    assert overlays == {"C0": set(), "C1": set(), "C2": set(),
                        "OUT_TOP": set(), "OUT_BOTTOM": set(),
                        "A": {"D01", "D02"}, "B": {"D12", "D02"}}
    values = {r.layer.name: r.values[0] for r in read_document(tmp_path / "out/materialmap.json").regions}
    for name in ("A", "B"):
        assert values[name][2] == pytest.approx(.6 * 385 + .4 * .3)
    for name in ("C0", "C1", "C2", "OUT_TOP", "OUT_BOTTOM"):
        assert values[name] == (.3, .3, .3, 1850, 1100)
    assert all(item["code"] == "materials.default_used" for item in report["diagnostics"])
    assert all(message.startswith("materials.default_used:") for message in summary["warnings"])


@pytest.mark.parametrize("change,match", [
    ("missing_row", "row"),
    ("duplicate_row", "duplicate matrix row"),
    ("reversed_copper", "copper_index order"),
    ("missing_endpoint", "existing copper_index"),
])
def test_invalid_matrix_topology_is_rejected_by_both_readers(tmp_path, change, match):
    document = topology_document()
    layers = {l["name"]: l for l in document["steps"][0]["layers"]}
    if change == "missing_row":
        del layers["A"]["row"]
    elif change == "duplicate_row":
        layers["A"]["row"] = layers["C0"]["row"]
    elif change == "reversed_copper":
        layers["C0"]["copper_index"], layers["C2"]["copper_index"] = 2, 0
    else:
        layers["D01"]["copper_span"] = [0, 3]
    with pytest.raises(ValueError, match=match):
        BoardDocument.model_validate(document)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        run(source, tmp_path / "out", px=20, py=20, max_cell_mm=[10])
    assert not (tmp_path / "out/materialmap.json").exists()


def test_unresolved_drill_span_fails_only_when_dielectric_sampling_is_needed(tmp_path):
    document = topology_document()
    layers = document["steps"][0]["layers"]
    del next(l for l in layers if l["name"] == "D01")["copper_span"]
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match='D01.*cannot resolve copper_span'):
        run(source, tmp_path / "out", px=20, py=20, max_cell_mm=[10])
    document["steps"][0]["layers"] = [l for l in layers if l["type"] != "DIELECTRIC"]
    source.write_text(json.dumps(document), encoding="utf-8")
    assert run(source, tmp_path / "out", px=20, py=20, max_cell_mm=[10])["layers"] == 3


def test_parser_preview_uses_matrix_rows_without_thickness():
    from pcbkit_rust.diagnostics import Diagnostics
    from pcbkit_rust.model import Layer, LayerRef, LayerType, Step
    from pcbkit_rust.stackup import Stackup

    endpoints = {"D01": ("C0", "C1"), "D12": ("C2", "C1"), "D02": ("C0", "C2")}
    layers = []
    for spec in topology_document()["steps"][0]["layers"]:
        start, end = endpoints.get(spec["name"], ("", ""))
        layers.append(Layer(LayerRef(name=spec["name"], row=spec["row"],
                                     type=LayerType(spec["type"]),
                                     start_name=start, end_name=end)))
    diagnostics = Diagnostics()
    stackup = Stackup(Step(name="pcb", layers=list(reversed(layers))), diagnostics=diagnostics)
    expected = {"OUT_TOP": set(), "A": {"D01", "D02"},
                "B": {"D12", "D02"}, "OUT_BOTTOM": set()}
    for layer in layers:
        if layer.name in expected:
            assert {d.name for d in stackup.drills_through(layer)} == expected[layer.name]
            span = stackup.dielectric_span(layer)
            assert (span is None) == layer.name.startswith("OUT_")
    assert not diagnostics.items
