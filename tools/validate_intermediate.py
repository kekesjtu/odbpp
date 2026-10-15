"""Compare JSON IO changes with a saved Git baseline in isolated processes.

uv run --all-extras python tools/validate_intermediate.py --baseline-ref <commit>
Measures actual export/import and full geometry/material equivalence separately;
compares unrounded properties, CSV, layer geometry, global axes and raw masks.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import statistics
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
    from odbpp import export
    from odbpp.equiv import equivalence_from_document, EquivalenceOptions, write_materialmap
    from odbpp.grid import GridOptions
    # Load the measurement helper by path so it works in a fresh worker process.
    from benchmark_pipeline import peak_memory_bytes

    output = args.workspace / args.case
    output.mkdir(parents=True, exist_ok=True)
    durations = {}

    def timed(name, call):
        start = time.perf_counter()
        result = call()
        durations[name] = time.perf_counter() - start
        return result

    job = timed("load", lambda: load_job(str(ROOT / "cases" / args.case)))
    preview = timed("compose", lambda: build_step_preview(job))
    path = output / "board.json"
    current = hasattr(export, "step_to_document")

    def write():
        if current:
            from odbpp.intermediate import write_document
            write_document(export.step_to_document(job, preview), path)
        else:
            export.write_json(export.step_to_json(job, preview), str(path))
    timed("json_export", write)
    del preview, job
    gc.collect()

    if current:
        from odbpp.intermediate import read_document
        document = timed("json_import", lambda: read_document(path))
        internal = document.internal_document()
    else:
        document = timed("json_import", lambda: json.loads(path.read_text(encoding="utf-8")))
        internal = document
    # Reduce the old/new interface to the same physical facts, without migrating
    # either consumer or introducing runtime compatibility code.
    step = internal["steps"][0]
    geometry = {
        "domains": [{k: d[k] for k in ("id", "source", "polygons")} for d in step["domains"]],
        "layers": [{"name": l["name"], "type": l["type"], "polygons": l["polygons"],
                    "domain_id": l.get("domain_id"),
                    # Compare independent physical facts, not a former redundant
                    # DRILL Z representation. Endpoint copper Z is checked below.
                    "copper_index": l.get("copper_index"), "copper_span": l.get("copper_span"),
                    "z_mm": None if l["type"] == "DRILL" or not l.get("z_mm") else {k: l["z_mm"][k] for k in
                         ("start_mm", "end_mm", "thickness_known", "source")}}
                   for l in step["layers"] if l.get("geometry_status", "ready") == "ready"],
    }
    geometry_hash = digest(geometry)
    del internal, step, geometry
    masks = {}

    def mask_sink(name, mask, pitch):
        masks[name] = {"sha256": hashlib.sha256(mask.pixels).hexdigest(), "pitch_mm": pitch}

    result = timed("equivalent", lambda: equivalence_from_document(document,
        EquivalenceOptions(GridOptions(1, 1, mesh_preset="standard", auto_raster=True)), mask_sink=mask_sink))
    if result.fatal:
        raise RuntimeError([d.message for d in result.diagnostics.errors])
    csv_path = output / "board.MaterialMap.json"
    timed("csv", lambda: write_materialmap(result, str(csv_path)))
    raw = hashlib.sha256()
    for cell in result.cells:
        raw.update(cell.layer_name.encode() + b"\0")
        raw.update(struct.pack("!6d", cell.copper_fraction, cell.kx, cell.ky, cell.kz, cell.density, cell.specific_heat))
    coords = ([r.bounds for r in result.rectification.rectangles], result.grid.x_lines,
              result.grid.y_lines, [(c.region_id, c.bounds) for c in result.grid.cells])
    return {"durations_s": durations, "peak_memory_mib": peak_memory_bytes() / 2**20,
            "json_bytes": path.stat().st_size, "geometry_sha256": geometry_hash,
            "grid_sha256": digest(coords), "properties_sha256": raw.hexdigest(),
            "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(), "masks": masks,
            "regions": len(result.rectification.rectangles), "cells": len(result.cells),
            "raster": [result.raster_px, result.raster_py]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workspace", type=Path, default=ROOT / "build/intermediate-validation")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/equivalence/intermediate-validation.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args), ensure_ascii=False))
        return
    if not args.baseline_ref or args.repeat < 1:
        parser.error("provide --baseline-ref and positive --repeat")
    from benchmark_pipeline import snapshot_source
    baseline = snapshot_source(args.baseline_ref, args.workspace / "baseline-source")
    report = {"baseline_ref": args.baseline_ref, "repeat": args.repeat,
              "measurement": "isolated processes; sequential baseline/current; no heatmap rendering", "cases": {}}
    for case in ([args.case] if args.case else CASES):
        records = {}
        for label, source in (("baseline", baseline), ("current", None)):
            runs = []
            for _ in range(args.repeat):
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--case", case,
                           "--workspace", str(args.workspace / label)]
                if source:
                    command += ["--source-root", source]
                done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
                runs.append(json.loads(done.stdout))
            records[label] = {"runs": runs, "median_s": {k: statistics.median(r["durations_s"][k] for r in runs)
                             for k in runs[0]["durations_s"]}}
        before, after = records["baseline"]["runs"][0], records["current"]["runs"][0]
        fields = ("geometry_sha256", "grid_sha256", "properties_sha256", "masks", "regions", "cells", "raster")
        records["checks"] = {field: all(r[field] == before[field] for label in records.values() for r in label["runs"])
                             for field in fields}
        if not all(records["checks"].values()):
            raise RuntimeError(f"physical output mismatch: {case}: {records['checks']}")
        report["cases"][case] = records
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{case}: physical outputs identical; JSON {before['json_bytes']} -> {after['json_bytes']} bytes", flush=True)


if __name__ == "__main__":
    main()
