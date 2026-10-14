"""Independent, design-derived assertions for the small KiCad 10 ODB++ set.

Ordinary pytest reads committed ZIPs; neither KiCad nor pcbnew is required.
Negative polarity, layer profiles, and recursive symbols remain in handwritten
fixtures because KiCad does not produce them for these boards.
"""

from __future__ import annotations

import math
from pathlib import Path
import re
from zipfile import ZipFile

import pytest

from odbpp import build_step_preview, load_job, step_to_json


ROOT = Path(__file__).resolve().parents[1] / "cases" / "kicad-positive"
EXPORTS = (
    "outline-only-mm", "pad-shapes-mm", "tracks-arcs-mm",
    "through-hole-mm", "through-hole-in", "zone-hole-rotation-mm",
    "four-layer-drills-mm",
)
BOARD_BOUNDS = (0.0, -10.0, 20.0, 0.0)  # KiCad Y-down -> ODB++ Y-up


def load(case: str, group: str = "clean"):
    job = load_job(str(ROOT / group / f"{case}.zip"))
    return job, build_step_preview(job)


def json_region(layer):
    from odbpp.clipper import region_from_rings
    return region_from_rings([(p["points"], not p["island"]) for p in layer["polygons"]])


def by_name(document: dict) -> dict[str, dict]:
    return {layer["name"]: layer for layer in document["steps"][0]["layers"]}


@pytest.mark.parametrize("case", EXPORTS)
def test_approved_native_and_clean_archives(case: str):
    native = ROOT / "native" / f"{case}.zip"
    clean = ROOT / "clean" / f"{case}.zip"
    for archive, is_clean in [(native, False), (clean, True)]:
        with ZipFile(archive) as z:
            assert z.testzip() is None
            info = z.read("misc/info").decode()
            assert "ODB_VERSION_MAJOR=8" in info
            assert "ODB_VERSION_MINOR=1" in info
            assert "ODB_SOURCE=KiCad EDA" in info
            assert ("UNITS=INCH" if case.endswith("-in") else "UNITS=MM") in info
            matrix = z.read("matrix/matrix").decode()
            assert matrix.count("STEP {") == 1
            assert re.search(r"F 1\s", z.read("steps/pcb/profile").decode())
            if is_clean:
                assert all(x not in matrix for x in ("TYPE=DOCUMENT", "TYPE=COMPONENT"))
                assert not any(n.startswith(("fonts/", "input/", "user/", "wheels/"))
                               for n in z.namelist())
            else:
                assert "NAME=EDGE.CUTS" in matrix
                assert "NAME=MARGIN" in matrix
        job, preview = load(case, "clean" if is_clean else "native")
        assert job.info.version.startswith("8.1")
        assert not job.diagnostics.errors
        doc = step_to_json(job, preview)
        assert doc["units"] == "mm"
        profile = doc["steps"][0]["domains"][0]["polygons"]
        assert len(profile) == 1 and profile[0]["island"]
        points = profile[0]["points"]
        assert (min(x for x, _ in points), min(y for _, y in points),
                max(x for x, _ in points), max(y for _, y in points)) == BOARD_BOUNDS
        for layer in doc["steps"][0]["layers"]:
            assert layer["parse_info"]["polarity"] == "P"
            if bool(layer["polygons"]) and layer["context"] == "BOARD":
                assert layer["z_mm"] is not None
                assert layer["z_mm"]["end_mm"] >= layer["z_mm"]["start_mm"]
                assert isinstance(layer["z_mm"]["thickness_known"], bool)
            for ring in layer["polygons"]:
                assert len(ring["points"]) >= 3
    # The curate step may change only the matrix and the ZIP member list.
    with ZipFile(native) as n, ZipFile(clean) as c:
        for member in c.namelist():
            if member != "matrix/matrix":
                assert c.read(member) == n.read(member)


def test_outline_only_has_no_copper_features():
    job, preview = load("outline-only-mm")
    assert [l.name for l in job.single_step.copper_layers] == ["F.CU", "B.CU"]
    assert all(l.feature_count == 0 for l in job.single_step.copper_layers)
    assert preview.layer("DIELECTRIC_1").area_mm2 == pytest.approx(200, abs=0.01)


def test_four_pad_shapes_are_from_distinct_design_sizes():
    job, preview = load("pad-shapes-mm")
    pads = job.single_step.layer("F.CU").features.records
    assert {(round(p.x_mm), round(p.y_mm), p.symbol.name) for p in pads} == {
        (3, -3, "r1000.0"), (7, -3, "rect1000.0x1000.0"),
        (11, -3, "rect2000.0x1000.0"), (16, -3, "oval2000.0x1000.0"),
    }
    # 1 mm circle + 1x1 square + 2x1 rectangle + 2x1 obround.
    expected_area = math.pi * .5**2 + 1 + 2 + (1 + math.pi * .5**2)
    assert preview.layer("F.CU").area_mm2 == pytest.approx(expected_area, abs=.03)


def test_line_and_arc_are_real_copper_features():
    job, preview = load("tracks-arcs-mm")
    line, arc = job.single_step.layer("F.CU").features.records
    assert line.kind == "line" and line.start_mm == (2, -3)
    assert line.end_mm == (7, -3) and line.symbol.name == "r200.0"
    assert arc.kind == "arc" and arc.center_mm == (13, -3)
    assert {arc.start_mm, arc.end_mm} == {(11, -3), (15, -3)}
    assert arc.symbol.name == "r200.0"
    assert 2.2 < preview.layer("F.CU").area_mm2 < 2.4


def test_same_source_through_via_converts_both_unit_systems():
    documents = {}
    for unit in ("mm", "in"):
        job, preview = load(f"through-hole-{unit}")
        drill = job.single_step.drill_layers[0]
        assert (drill.ref.start_name, drill.ref.end_name) == ("F.CU", "B.CU")
        assert next(iter(drill.tools.tools.values())).drill_size_mm == pytest.approx(.4, abs=.0001)
        assert preview.layer("DIELECTRIC_1").area_mm2 == pytest.approx(
            200, abs=.006)
        documents[unit] = by_name(step_to_json(job, preview))
    for name in ("F.CU", "B.CU", "DIELECTRIC_1", "DRILL_PLATED_F.CU-B.CU"):
        mm, inch = documents["mm"][name], documents["in"][name]
        assert json_region(mm).bounds == pytest.approx(json_region(inch).bounds, abs=.0001)
        assert json_region(mm).area == pytest.approx(json_region(inch).area, abs=.001)
    metric_weight = documents["mm"]["F.CU"]["thickness"]
    imperial_weight = documents["in"]["F.CU"]["thickness"]
    assert (metric_weight["kind"], metric_weight["unit"]) == ("thickness_um", "um")
    assert (documents["mm"]["F.CU"]["z_mm"]["end_mm"] - documents["mm"]["F.CU"]["z_mm"]["start_mm"]) == pytest.approx(.001)
    assert (imperial_weight["kind"], imperial_weight["unit"]) == (
        "areal_density_oz_ft2", "oz/ft2")
    assert imperial_weight["thickness_source"] == "assumed_density"
    assert imperial_weight["assumed_density_g_cm3"] == 8.96
    assert (documents["in"]["F.CU"]["z_mm"]["end_mm"] - documents["in"]["F.CU"]["z_mm"]["start_mm"]) == pytest.approx(.0340571, abs=.000001)


def test_filled_zone_closed_hole_and_rotated_pad():
    job, preview = load("zone-hole-rotation-mm")
    pad, zone = job.single_step.layer("F.CU").features.records
    assert (pad.kind, zone.kind) == ("pad", "surface")
    assert pad.x_mm == 16 and pad.y_mm == -5
    assert pad.orientation.rotation_deg == 330  # ODB Y axis reverses KiCad 30°
    copper = preview.layer("F.CU")
    assert sum(r.is_hole for r in copper.rings) == 1
    assert copper.area_mm2 == pytest.approx(10 * 6 - 2 * 2 + 2 * 1, abs=.02)
    # KiCad fractures a closed polygon hole with a zero-width slit in ODB++.
    # The normalised output must reconstruct the inner empty ring.
    document = by_name(step_to_json(job, preview))["F.CU"]
    assert sum(not polygon["island"] for polygon in document["polygons"]) == 1


def test_four_copper_layers_and_three_distinct_drill_spans():
    job, preview = load("four-layer-drills-mm")
    step = job.single_step
    assert [l.name for l in step.copper_layers] == ["F.CU", "IN1.CU", "IN2.CU", "B.CU"]
    spans = {(l.ref.start_name, l.ref.end_name) for l in step.drill_layers}
    assert spans == {("F.CU", "B.CU"), ("F.CU", "IN1.CU"),
                     ("IN1.CU", "IN2.CU")}
    for layer in step.drill_layers:
        assert layer.feature_count == 1
        assert next(iter(layer.tools.tools.values())).drill_size_mm == pytest.approx(.4)
    from odbpp.drawing import dielectric_drill_cutouts
    drawing = dielectric_drill_cutouts(preview)
    cut = {l.name: set(l.geometry.drills_subtracted) for l in drawing.layers
           if l.name.startswith("DIELECTRIC_")}
    through = "DRILL_PLATED_F.CU-B.CU"
    blind = "DRILL_PLATED_F.CU-IN1.CU"
    buried = "DRILL_PLATED_IN1.CU-IN2.CU"
    assert cut == {"DIELECTRIC_1": {through, blind},
                   "DIELECTRIC_2": {through, buried},
                   "DIELECTRIC_3": {through}}
    for name, count in (("DIELECTRIC_1", 2), ("DIELECTRIC_2", 2),
                        ("DIELECTRIC_3", 1)):
        assert sum(r.is_hole for r in drawing.layer(name).rings) == count
        assert not preview.layer(name).geometry.drills_subtracted
        assert not preview.layer(name).geometry.region.holes


@pytest.mark.parametrize("case", ("outline-only", "pad-shapes", "tracks-arcs",
                                  "through-hole", "zone-hole-rotation", "four-layer-drills"))
def test_source_board_enables_only_copper_and_edges(case: str):
    source = (ROOT / "sources" / f"{case}.kicad_pcb").read_text(encoding="utf-8")
    layer_block = source.split("\n\t(layers\n", 1)[1].split("\n\t)", 1)[0]
    assert '"F.Cu"' in layer_block and '"B.Cu"' in layer_block
    assert '"Edge.Cuts"' in layer_block
    if case == "four-layer-drills":
        assert '"In1.Cu"' in layer_block and '"In2.Cu"' in layer_block
    else:
        assert '"In1.Cu"' not in layer_block
    # KiCad 10 reinstates three mandatory empty technical layers on save.
    assert set(re.findall(r'\(\d+ "([^"]+)"', layer_block)) == (
        {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu", "Edge.Cuts", "Margin",
         "F.CrtYd", "B.CrtYd"} if case == "four-layer-drills" else
        {"F.Cu", "B.Cu", "Edge.Cuts", "Margin", "F.CrtYd", "B.CrtYd"})
    for pad_layers in re.findall(r"\(pad [\s\S]*?\(layers ([^)]+)\)", source):
        assert pad_layers == '"F.Cu"'
