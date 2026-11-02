"""Benchmark intermediate JSON reading through closed MaterialMap output, without images.

Example (from the repository root):
    uv run python tools/benchmark_pcbeq.py out/minimal-test-odb/python/pcbkit/intermediate.json \
        --output tmp/2026-11-02-pcbeq-benchmark/python --repeat 3
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

from pcbeq import numerical
from pcbeq.diagnostics import Diagnostics
from pcbeq.equiv import EquivalenceError, EquivalenceOptions, equivalence_from_document, pipeline_fingerprint
from pcbeq.grid import GridOptions
from pcbeq.intermediate import read_document
from pcbeq.materialmap import MaterialMapWriter
from pcbeq.materials import MaterialBook
from pcbeq.rectify import RectifyOptions


def measure(source: Path, output: Path, grid: GridOptions, materials: dict) -> dict:
    """Use the production streaming writer; no mask sink or visualization imports.

    Reading, validation, geometry, sampling, properties and streamed JSON writing
    overlap during equivalence, so computation and writing form one timed stage.
    Hashing, statistics and benchmark reporting happen after the timed interval.
    """
    diagnostics = Diagnostics()
    options = EquivalenceOptions(grid=grid, materials=MaterialBook(materials, diagnostics=diagnostics),
                                 rectify=RectifyOptions())
    final = output / "materialmap.json"
    temporary = output / "materialmap.json.tmp"
    gc.collect()
    try:
        cpu_start = time.process_time()
        start = time.perf_counter()
        document = read_document(source)
        read_end = time.perf_counter()
        with MaterialMapWriter(temporary, step_name=document.steps[0].name) as writer:
            result = equivalence_from_document(document, options, diagnostics=diagnostics,
                                               cell_sink=writer.add)
            if result.fatal or diagnostics.errors:
                raise ValueError("untrustworthy equivalence: " + "; ".join(map(str, diagnostics.errors)))
            writer.finish(result)
        temporary.replace(final)
        end = time.perf_counter()
        cpu_end = time.process_time()
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "durations_s": {"read_json": read_end - start,
                        "equivalence_and_write": end - read_end, "total": end - start},
        "cpu_s": cpu_end - cpu_start,
        "cells_per_s": writer.count / (end - start),
        "cells": writer.count,
        "regions": len(writer.headers),
        "layers": sum(layer.status == "equivalent" for layer in result.layers),
        "raster": [result.raster_px, result.raster_py],
        "materialmap_bytes": final.stat().st_size,
        "materialmap_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "warnings": [str(warning) for warning in diagnostics.warnings],
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="existing intermediate JSON; no ODB++ parsing")
    parser.add_argument("--output", type=Path, required=True,
                        help="benchmark.json and the final materialmap.json directory")
    parser.add_argument("--backend", choices=("python", "rust"), default="python")
    parser.add_argument("--repeat", type=int, default=3, help="measured runs (default: 3)")
    parser.add_argument("--warmup", type=int, default=1, help="unmeasured runs (default: 1)")
    parser.add_argument("--px", type=int)
    parser.add_argument("--py", type=int)
    mesh = parser.add_mutually_exclusive_group()
    mesh.add_argument("--grid-level", choices=("coarse", "standard", "fine"))
    mesh.add_argument("--max-cell-mm", type=float, nargs="+", metavar="MM")
    parser.add_argument("--materials", type=Path, help="production material configuration JSON")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat must be positive and warmup must be nonnegative")
    if (args.px is None) != (args.py is None):
        parser.error("--px and --py must be provided together")
    if args.input.resolve() in {args.output.resolve() / name for name in
                                ("materialmap.json", "materialmap.json.tmp", "benchmark.json")}:
        parser.error("input must not be overwritten by benchmark outputs")
    try:
        grid = GridOptions(px=4096 if args.px is None else args.px,
                           py=4096 if args.py is None else args.py,
                           auto_raster=args.px is None,
                           mesh_preset=args.grid_level or ("standard" if args.max_cell_mm is None else None),
                           max_cell_mm=args.max_cell_mm if args.max_cell_mm is not None else 5)
        materials = json.loads(args.materials.read_text(encoding="utf-8")) if args.materials else {}
        if not isinstance(materials, dict):
            raise ValueError("materials must be a JSON object")
        source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
        context = numerical.use(None)
        if args.backend == "rust":
            from pcbeq_rust import backend
            context = backend()
        args.output.mkdir(parents=True, exist_ok=True)
        with context:
            report = {
                "measurement": "intermediate JSON read/validation through MaterialMap close and rename",
                "excluded": ["ODB++ parsing", "process startup/imports", "images/HTML",
                             "benchmark metadata and hashing", "fsync to physical storage"],
                "cache_policy": "same process; normal OS file cache; fresh document/options per run",
                "environment": {"python": sys.version, "platform": platform.platform(),
                                "processor": platform.processor(), "cpu_count": os.cpu_count()},
                "backend": args.backend,
                "implementation_sha256": pipeline_fingerprint(),
                "input": {"path": str(args.input.resolve()), "sha256": source_hash,
                          "bytes": args.input.stat().st_size},
                "options": {"px": grid.px, "py": grid.py, "auto_raster": grid.auto_raster,
                            "grid_level": grid.mesh_preset, "max_cell_mm": grid.max_cell_mm,
                            "materials": materials},
                "warmup": args.warmup,
                "repeat": args.repeat,
                "runs": [],
            }
            expected_hash = None
            for index in range(args.warmup + args.repeat):
                run = measure(args.input, args.output, grid, materials)
                if expected_hash is not None and run["materialmap_sha256"] != expected_hash:
                    raise ValueError("non-deterministic MaterialMap output between runs")
                expected_hash = run["materialmap_sha256"]
                warmup = index < args.warmup
                if not warmup:
                    report["runs"].append(run)
                print(f"{'warmup' if warmup else 'run'} {index + 1}: "
                      f"{run['durations_s']['total']:.6f} s, {run['cells']} cells", flush=True)
            report["summary_s"] = {
                stage: {"median": statistics.median(values), "min": min(values), "max": max(values)}
                for stage in ("read_json", "equivalence_and_write", "total")
                for values in [[run["durations_s"][stage] for run in report["runs"]]]}
        (args.output / "benchmark.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for warning in report["runs"][-1]["warnings"]:
            print(warning, file=sys.stderr)
        print(f"median: {report['summary_s']['total']['median']:.6f} s; "
              f"report: {args.output / 'benchmark.json'}")
        return 0
    except (OSError, ValueError, ImportError, EquivalenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
