"""Reproduce third-review export and Z metadata issues without editing cases."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from odbpp import PreviewOptions, build_step_preview, load_job, step_to_json, write_outputs
from odbpp.clipper import Region, Ring, to_surface_geometry
from odbpp.features import SurfaceFeature


def main():
    job = load_job("cases/minimal-test-odb")
    layer = job.single_step.layers[0]
    narrow = Ring(((0, 0), (0, 1), (0.00002, 1), (0.00002, 0)), False)
    layer.features.records = [SurfaceFeature(
        line_no=1, geometry=to_surface_geometry(Region([narrow])))]
    preview = build_step_preview(job, options=PreviewOptions(
        layers=[layer.name], round_digits=6))
    with TemporaryDirectory(prefix="review3-", dir="build") as temp:
        files = write_outputs(preview, job, temp)
        document = json.loads(Path(files.json).read_text(encoding="utf-8"))
        svg = ET.parse(files.svg)
        group = next(g for g in svg.getroot().iter("{http://www.w3.org/2000/svg}g")
                     if g.get("data-name") == layer.name)
        path = group.find("{http://www.w3.org/2000/svg}path").get("d")
        html = Path(files.html).read_text(encoding="utf-8")
        output = {"preview_precision": {
            "model_digits": preview.round_digits,
            "json_points": document["steps"][0]["layers"][0]["polygons"][0]["points"],
            "svg_path": path,
            "same_collapsed_path_in_html": path in html,
        }}

    coarse = step_to_json(job, preview, round_digits=4)
    output["coarse_summary"] = {
        "actual_polygon_count": sum(len(l["polygons"]) for l in coarse["steps"][0]["layers"]),
        "reported_summary": coarse["summary"],
        "step_bounds": coarse["steps"][0]["bounds_mm"],
        "layer_bounds": coarse["steps"][0]["layers"][0]["bounds_mm"],
    }

    kitchen = load_job("cases/kicad-kitchen-sink")
    kitchen_preview = build_step_preview(kitchen, options=PreviewOptions(layers=["F.PASTE"]))
    paste = step_to_json(kitchen, kitchen_preview)["steps"][0]["layers"][0]
    output["missing_paste_thickness"] = {
        "name": paste["name"], "polygon_count": len(paste["polygons"]),
        "thickness_mm": paste["thickness_mm"], "z_mm": paste["z_mm"],
    }
    target = Path("out/review3-evidence.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
