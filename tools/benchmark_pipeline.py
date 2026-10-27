"""Measure the two independent CLI processes and compare numerical deliverables."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics
try:
    from tools.run_pipeline import execute, forwarded
except ModuleNotFoundError:
    from run_pipeline import execute, forwarded

ROOT = Path(__file__).resolve().parents[1]
CASES = ("minimal-test-odb", "kicad-kitchen-sink", "kicad-video-odb.tgz", "designodb-rigidflex.tgz")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--pcbkit-exe")
    parser.add_argument("--pcbeq-exe")
    parser.add_argument("--px", type=int)
    parser.add_argument("--py", type=int)
    parser.add_argument("--grid-level", choices=("coarse", "standard", "fine"))
    parser.add_argument("--max-cell-mm", type=float, nargs="+")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    baseline = json.loads(args.compare.read_text(encoding="utf-8")) if args.compare else None
    _, options = forwarded(args)
    report = {"measurement": "CLI wall time including imports, IO and all requested images", "cases": {}}
    for case in args.case or CASES:
        output = args.output / case
        runs = []
        for _ in range(args.repeat):
            result = execute(ROOT / "cases" / case, output, equivalent_args=options,
                             pcbkit_exe=args.pcbkit_exe, pcbeq_exe=args.pcbeq_exe)
            if result["status"] != "success":
                raise RuntimeError(result)
            meta = json.loads((output / "pcbeq/diagnostics/run.json").read_text(encoding="utf-8"))
            numerical = [output / "pcbkit/intermediate.json", output / "pcbeq/materialmap.json"]
            numerical += sorted((output / "pcbeq/masks").glob("*.png"))
            hashes = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in numerical}
            record = {"durations_s": {s["name"]: s["elapsed_s"] for s in result["stages"]},
                      "peak_memory_bytes": {"pcbkit": json.loads((output / "pcbkit/diagnostics/run.json").read_text(encoding="utf-8"))["runtime"]["peak_memory_bytes"],
                                            "pcbeq": meta["runtime"]["peak_memory_bytes"]},
                      "files": hashes, "regions": len(meta["rectification"]["rectangles"]),
                      "cells": meta["grid"]["cell_count"], "raster": [meta["raster"]["px"], meta["raster"]["py"]]}
            if runs and hashes != runs[0]["files"]:
                raise RuntimeError(f"non-deterministic numerical output: {case}")
            runs.append(record)
        if baseline and runs[0]["files"] != baseline["cases"][case]["runs"][0]["files"]:
            raise RuntimeError(f"numerical output mismatch: {case}")
        report["cases"][case] = {"runs": runs, "median_s": {
            stage: statistics.median(r["durations_s"][stage] for r in runs) for stage in ("pcbkit", "pcbeq")}}
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
