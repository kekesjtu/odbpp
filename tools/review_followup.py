"""Reproduce the topology issues found in the second review (2026-09-21)."""
from __future__ import annotations

import json
from pathlib import Path

import pyclipper
from shapely.geometry import Polygon

from odbpp import PreviewOptions, build_step_preview, load_job, step_to_json
from odbpp.clipper import Region, Ring, snap_to_grid, to_surface_geometry
from odbpp.features import SurfaceFeature
from odbpp.features import ArcFeature, FeatureFile, SymbolRef
from odbpp.expand import Expander
from odbpp.units import UnitScope
from math import pi


def main():
    # A simple polygon with two large lobes connected by a sub-grid neck.
    points = [(0, 0), (2, 0), (2, 2), (1.00002, 2), (1.00002, 3),
              (2, 3), (2, 5), (0, 5), (0, 3), (1, 3), (1, 2), (0, 2)]
    ring = Ring(tuple(reversed(points)), False)
    actual, collapsed = snap_to_grid(Region([ring]), 4)
    # Preserve the complete walk when snapping. Both lobes must survive;
    # only the collapsed connection has zero area.
    grid_path = [(round(x * 10000), round(y * 10000)) for x, y in ring.points]
    expected_parts = pyclipper.SimplifyPolygon(grid_path, pyclipper.PFT_NONZERO)
    output = {
        "nonadjacent_vertex_removal": {
            "input_valid": Polygon(points).is_valid,
            "input_area_mm2": ring.area,
            "expected_grid_area_mm2": sum(abs(pyclipper.Area(p)) / 1e8 for p in expected_parts),
            "actual_grid_area_mm2": actual.area,
            "output_rings_valid": all(Polygon(r.points).is_valid for r in actual.rings),
            "collapsed_reported": collapsed,
            "input_points": points,
            "output_points": [r.points for r in actual.rings],
        }
    }

    # Use the public preview/JSON APIs with different, supported resolutions.
    # The source job is changed in memory only; cases/ remains untouched.
    job = load_job("cases/minimal-test-odb")
    layer = job.single_step.layers[0]
    narrow = Ring(((0, 0), (0, 1), (0.00002, 1), (0.00002, 0)), False)
    layer.features.records = [SurfaceFeature(
        line_no=1, geometry=to_surface_geometry(Region([narrow])))]
    preview = build_step_preview(job, options=PreviewOptions(
        layers=[layer.name], round_digits=6))
    document = step_to_json(job, preview)
    written = document["steps"][0]["layers"][0]
    polygon = Polygon(written["polygons"][0]["points"])
    output["serializer_resolution_mismatch"] = {
        "preview_area_mm2": preview.layers[0].area_mm2,
        "reported_area_mm2": written["area_mm2"],
        "written_area_mm2": polygon.area,
        "written_polygon_valid": polygon.is_valid,
        "written_points": written["polygons"][0]["points"],
    }
    arc = ArcFeature(line_no=1, start_mm=(5, 0), end_mm=(5, 0),
                     center_mm=(0, 0), symbol=SymbolRef(0, "r100"))
    features = FeatureFile(path="review/full-circle", units=UnitScope.metric(),
                           records=[arc])
    expander = Expander(None)
    circle = expander.run_region(features.records, features, depth=0)
    output["full_circle_closure"] = {
        "stroke_width_mm": 0.1, "radius_mm": 5,
        "expected_area_mm2": pi, "actual_area_mm2": circle.area,
        "expected_holes": 1, "actual_holes": len(circle.holes),
        "diagnostics": [d.code for d in expander.diagnostics],
    }
    target = Path("out/review2-evidence.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
