"""Reproduce the 2026-09-21 review findings without modifying case inputs."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from odbpp import clipper
from odbpp.expand import Expander
from odbpp.features import ArcFeature, FeatureFile, SymbolRef
from odbpp.units import UnitScope, UnitSystem
from odbpp.cli import main as cli_main
from odbpp.export import _points_json
from shapely.geometry import Polygon


def main():
    output = {}
    feature = ArcFeature(line_no=1, start_mm=(5, 0), end_mm=(5, 0),
                         center_mm=(0, 0), symbol=SymbolRef(0, "r1000"))
    features = FeatureFile(path="review/full-circle", units=UnitScope(UnitSystem.MM),
                           records=[feature])
    expander = Expander(None)
    circle = expander.run_region(features.records, features, depth=0)
    output["full_circle"] = {"expected_area_mm2": 10 * math.pi,
                             "actual_area_mm2": circle.area,
                             "diagnostics": [d.code for d in expander.diagnostics]}

    def square(x1, x2):
        return clipper.region_from_rings([([(x1, 0), (x2, 0), (x2, 2), (x1, 2)], False)])

    a, b, c = square(0, 2), square(0, 1), square(1, 2)
    output["intersection"] = {
        "with_empty_expected": 0, "with_empty_actual": clipper.intersection(a, clipper.Region()).area,
        "three_regions_expected": 0, "three_regions_actual": clipper.intersection(a, b, c).area,
    }
    narrow = clipper.Ring(((0, 0), (0, 1), (0.00002, 1), (0.00002, 0)), False)
    rounded = Polygon(_points_json(narrow, 4))
    snapped, collapsed = clipper.snap_to_grid(clipper.Region([narrow]), 4)
    output["rounding"] = {
        # Per-point rounding in isolation is still unsafe -- that is *why* the
        # pipeline no longer feeds it unsnapped geometry.
        "before_valid": Polygon(narrow.points).is_valid,
        "before_area": narrow.area,
        "after_valid": rounded.is_valid,
        "after_area": rounded.area,
        # The fix: quantise as geometry, then serialise.  A ring with no area at
        # the target resolution is dropped and counted instead of written out.
        "pipeline_region_empty": not snapped,
        "pipeline_collapsed_rings": collapsed,
        "pipeline_rings": len(snapped.rings),
    }
    with TemporaryDirectory(prefix="review-cli-", dir="build") as temporary:
        exit_code = cli_main(["export", "cases/minimal-test-odb", "-o", temporary,
                              "--only-json", "--no-diagnostics"])
        cli_document = json.loads(next(Path(temporary).glob("*.json")).read_text(encoding="utf-8"))
        output["no_diagnostics_flag"] = {
            "exit_code": exit_code, "diagnostics_key_present": "diagnostics" in cli_document}
    cases = {}
    for folder in sorted(Path("out").iterdir()):
        if not folder.is_dir():
            continue
        documents = list(folder.glob("*.json"))
        if len(documents) != 1:
            continue
        doc = json.loads(documents[0].read_text(encoding="utf-8"))
        if doc.get("schema") != "odbpp/preview-1":
            continue
        layers = doc["steps"][0]["layers"]
        svg_paths = list(folder.rglob("*.svg"))
        for svg_path in svg_paths:
            for _, element in ET.iterparse(svg_path, events=("end",)):
                element.clear()
        errors = []
        for layer in layers:
            for index, ring in enumerate(layer["polygons"]):
                poly = Polygon(ring["points"])
                if not poly.is_valid or poly.area == 0:
                    errors.append({"layer": layer["name"], "ring": index,
                                   "area": poly.area, "valid": poly.is_valid})
        def region(layer):
            return clipper.region_from_rings([(p["points"], not p["island"])
                                              for p in layer["polygons"]])

        def span(layer):
            z = layer.get("z_span")
            return z["copper_index"] if z else None

        def overlaps(a, b):
            return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]

        drills = [layer for layer in layers if layer["type"] == "DRILL"]
        overlaps_out = []
        for layer in layers:
            if layer["type"] == "DIELECTRIC":
                # ``drill_xy_overlap_mm2`` is every drill whose *projection*
                # overlaps, ignoring Z; ``crossing_*`` restricts the same
                # measurement to the drills the stackup says actually pass
                # through this dielectric (AGENTS constraint 3).
                xy = {drill["name"]: clipper.intersection(region(layer), region(drill)).area
                      for drill in drills if drill["polygons"] and layer["polygons"]}
                crossing = {name: value for name, value in xy.items()
                            if overlaps(span(layer),
                                        span(next(d for d in drills
                                                  if d["name"] == name)))}
                overlaps_out.append({
                    "layer": layer["name"],
                    "area_mm2": layer["area_mm2"],
                    "attributes": layer["attributes"],
                    "thickness_mm": layer["thickness_mm"],
                    "z_span": layer["z_span"],
                    "drills_subtracted": layer["drills_subtracted"],
                    "drill_xy_overlap_mm2": xy,
                    "crossing_drill_overlap_mm2": crossing,
                })
        cases[folder.name] = {
            "summary": doc["summary"],
            "diagnostic_levels": dict(Counter(d["level"] for d in doc.get("diagnostics", []))),
            "diagnostic_codes": dict(Counter(d["code"] for d in doc.get("diagnostics", []))),
            "invalid_ring_count": len(errors), "invalid_ring_examples": errors[:10],
            "invalid_rings_by_layer": dict(Counter(e["layer"] for e in errors)),
            "dielectrics": overlaps_out,
            "svg_count": len(svg_paths),
            "svg_xml_valid": True,
        }
    output["cases"] = cases
    target = Path("out/review-evidence.json")
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
