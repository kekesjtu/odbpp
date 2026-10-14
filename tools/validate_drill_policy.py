"""Replay the dielectric-only DRILL policy against an isolated Git baseline.

uv run --all-extras python tools/validate_drill_policy.py --baseline-ref 9db11c7
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ("minimal-test-odb", "kicad-kitchen-sink", "kicad-video-odb.tgz", "designodb-rigidflex.tgz")


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def worker(args):
    if args.source_root:
        sys.path.insert(0, str(args.source_root.resolve()))
    from odbpp import load_job, build_step_preview
    from odbpp.export import step_to_document
    from odbpp.intermediate import write_document, read_document
    from odbpp.equiv import equivalence_from_document, EquivalenceOptions, COPPER_TYPES
    from odbpp.grid import GridOptions
    from odbpp.svg import render_svg
    from benchmark_pipeline import peak_memory_bytes

    directory = args.workspace / args.case
    directory.mkdir(parents=True, exist_ok=True)
    durations = {}

    def timed(name, call):
        start = time.perf_counter()
        value = call()
        durations[name] = time.perf_counter() - start
        return value

    job = timed("load", lambda: load_job(str(ROOT / "cases" / args.case)))
    preview = timed("compose", lambda: build_step_preview(job))
    timed("svg", lambda: render_svg(preview))
    if not args.source_root:
        from odbpp.drawing import dielectric_drill_cutouts
        drawing = dielectric_drill_cutouts(preview)
    else:
        drawing = preview
    drawing_geometry = {l.name: {"area_mm2": l.geometry.region.area,
        "polygons": [{"points": r.points, "island": not r.is_hole} for r in l.rings]}
        for l in drawing.layers if l.type_name == "DIELECTRIC"}
    (directory / "drawing.json").write_text(json.dumps(drawing_geometry), encoding="utf-8")
    path = directory / "board.json"
    timed("json_export", lambda: write_document(step_to_document(job, preview), path))
    document = timed("json_import", lambda: read_document(path))
    step = document.internal_document()["steps"][0]
    layers = step["layers"]
    geometry = {"domains": step["domains"], "profile_domain_id": step["profile_domain_id"],
        "copper": [{k: l.get(k) for k in ("name", "type", "polygons", "domain_id")}
                   for l in layers if l["type"] in COPPER_TYPES],
        "stack": [{k: l.get(k) for k in ("name", "type", "z_mm", "domain_id", "copper_span", "copper_index")}
                  for l in layers]}
    masks = {}

    def mask_sink(name, mask, pitch):
        masks[name] = hashlib.sha256(mask.pixels).hexdigest()

    options = EquivalenceOptions(GridOptions(1, 1, mesh_preset="standard", auto_raster=True))
    result = timed("equivalent", lambda: equivalence_from_document(document, options, mask_sink=mask_sink))
    if result.fatal:
        raise RuntimeError([d.message for d in result.diagnostics.errors])

    def property_hashes(result, *, save=False):
        values = {}
        for cell in result.cells:
            values.setdefault(cell.layer_name, bytearray()).extend(struct.pack("!6d",
                cell.copper_fraction, cell.kx, cell.ky, cell.kz, cell.density, cell.specific_heat))
        if save:
            for name, value in values.items():
                (directory / (hashlib.sha256(name.encode()).hexdigest() + ".bin")).write_bytes(value)
        return {name: hashlib.sha256(value).hexdigest() for name, value in values.items()}

    properties = property_hashes(result, save=True)
    primary_peak_mib = peak_memory_bytes() / 2**20
    no_drill_checks = None
    if not args.source_root:
        reference = deepcopy(step)
        reference["layers"] = [l for l in reference["layers"] if l["type"] != "DRILL"]
        reference_masks = {}
        ref = timed("no_drill_reference", lambda: equivalence_from_document(
            {"units": "mm", "steps": [reference]}, options,
            mask_sink=lambda name, mask, pitch: reference_masks.update({name: hashlib.sha256(mask.pixels).hexdigest()})))
        if ref.fatal:
            raise RuntimeError([d.message for d in ref.diagnostics.errors])
        hashes = property_hashes(ref)
        no_drill_checks = {l["name"]: masks[l["name"]] == reference_masks[l["name"]]
            and properties[l["name"]] == hashes[l["name"]]
            for l in layers if l["type"] in COPPER_TYPES and l["name"] in properties}
    coords = ([r.bounds for r in result.rectification.rectangles], result.grid.x_lines,
              result.grid.y_lines, [(c.region_id, c.bounds) for c in result.grid.cells])
    meta = result.metadata()
    return {"durations_s": durations, "peak_memory_mib": primary_peak_mib,
        "json_bytes": path.stat().st_size, "geometry_sha256": digest(geometry),
        "grid_sha256": digest(coords), "masks": masks, "properties": properties,
        "layer_types": {l["name"]: l["type"] for l in layers},
        "drill_overlays": {l.name: l.drill_overlays for l in result.layers if l.name in properties},
        "no_drill_copper_checks": no_drill_checks, "drill_scope": meta.get("drill_scope"),
        "regions": len(result.rectification.rectangles), "cells": len(result.cells),
        "raster": [result.raster_px, result.raster_py]}


def compare(args, case, before, after):
    import numpy as np
    from odbpp.equiv import _region_from_polygons
    from odbpp.rectify import profile_shape
    copper = {n for n in after["properties"] if after["layer_types"][n] != "DIELECTRIC"}
    dielectric = set(after["properties"]) - copper
    checks = {k: before[k] == after[k] for k in ("geometry_sha256", "grid_sha256", "regions", "cells", "raster")}
    checks["dielectric_masks_and_properties"] = all(before["masks"][n] == after["masks"][n]
        and before["properties"][n] == after["properties"][n] for n in dielectric)
    checks["copper_matches_no_drill_reference"] = all(after["no_drill_copper_checks"].values())
    checks["dielectric_only_scope"] = after["drill_scope"] == "dielectric_only" and all(
        not after["drill_overlays"][n] for n in copper)
    drawings = [json.loads((args.workspace / label / case / "drawing.json").read_text(encoding="utf-8"))
                for label in ("baseline", "current")]
    display = {}
    for name in drawings[0]:
        shapes = [profile_shape(_region_from_polygons(d[name]["polygons"])) for d in drawings]
        error = shapes[0].symmetric_difference(shapes[1]).area
        # Old code subtracts before coordinate quantisation, new renderer after it.
        budget = 2e-4 * max(s.length for s in shapes)
        display[name] = {"symmetric_difference_mm2": error, "quantisation_budget_mm2": budget,
                         "before_area_mm2": shapes[0].area, "after_area_mm2": shapes[1].area}
    checks["dielectric_display_within_coordinate_precision"] = all(
        row["symmetric_difference_mm2"] <= row["quantisation_budget_mm2"] + 1e-8 for row in display.values())
    changes = {}
    for name in sorted(copper):
        file = hashlib.sha256(name.encode()).hexdigest() + ".bin"
        a, b = [np.fromfile(args.workspace / label / case / file, dtype=">f8").reshape(-1, 6)
                for label in ("baseline", "current")]
        changes[name] = {"changed_cells": int((a != b).any(axis=1).sum()),
            "max_absolute_change": dict(zip(("copper_fraction", "kx", "ky", "kz", "density", "specific_heat"),
                                            np.abs(a-b).max(axis=0).tolist()))}
    if not all(checks.values()):
        raise RuntimeError(f"{case}: {checks}")
    return {"checks": checks, "copper_intended_changes": changes, "dielectric_display": display}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--workspace", type=Path, default=ROOT / "build/drill-policy-validation")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/equivalence/drill-policy-validation.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args), ensure_ascii=False))
        return
    if not args.baseline_ref:
        parser.error("provide --baseline-ref")
    from benchmark_pipeline import snapshot_source
    baseline = snapshot_source(args.baseline_ref, args.workspace / "baseline-source")
    report = {"baseline_ref": args.baseline_ref, "drill_scope": "dielectric_only",
        "measurement": "isolated sequential processes; single run; timings are observations on a machine also regenerating case outputs",
        "peak_memory_scope": "primary pipeline before additional no-DRILL reference",
        "cases": {}}
    for case in ([args.case] if args.case else CASES):
        records = {}
        for label, source in (("baseline", baseline), ("current", None)):
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--case", case,
                       "--workspace", str(args.workspace / label)]
            if source:
                command += ["--source-root", source]
            done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
            records[label] = json.loads(done.stdout)
        records.update(compare(args, case, records["baseline"], records["current"]))
        report["cases"][case] = records
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{case}: dielectric sampling and geometry unchanged; copper matches no-DRILL reference", flush=True)


if __name__ == "__main__":
    main()
