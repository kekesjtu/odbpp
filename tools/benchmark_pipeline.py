"""Measure the unchanged pipeline and compare exact artefact fingerprints.

Run with uv run --all-extras python tools/benchmark_pipeline.py --output build/perf.
--source-root can select a saved src/ tree without changing the checkout.
Each repeat runs in a fresh process; profiling is separate from timing runs.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ("minimal-test-odb", "kicad-kitchen-sink", "kicad-video-odb.tgz",
         "designodb-rigidflex.tgz")


def peak_memory_bytes():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak", "working", "quota_peak_paged", "quota_paged",
                    "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def snapshot_source(ref, directory):
    """Read a Git revision into scratch without switching the working tree."""
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", ref+"^{commit}"],
        cwd=ROOT, text=True).strip()
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "--", "src/odbpp"],
        cwd=ROOT, text=True).splitlines()
    if not names:
        raise RuntimeError(f"no odbpp source in {ref}")
    for name in names:
        path = directory/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(["git", "show", revision+":"+name], cwd=ROOT))
    return str((directory/"src").resolve())


def worker(args):
    if args.source_root:
        sys.path.insert(0, str(Path(args.source_root).resolve()))
    from odbpp import load_job, build_step_preview, write_outputs
    from odbpp.equiv import equivalence_from_document, EquivalenceOptions, write_materialmap
    from odbpp.grid import GridOptions
    from odbpp.visualize import write_layer_mask, render_heatmaps, unique_image_stem

    output = Path(args.output) / args.case[0]
    output.mkdir(parents=True, exist_ok=True)
    durations = {}
    artefacts = []

    def timed(label, call):
        start = time.perf_counter()
        value = call()
        durations[label] = time.perf_counter() - start
        return value

    case = args.case[0]
    if args.mode in ("preview", "all"):
        job = timed("load", lambda: load_job(str(Path("cases") / case)))
        preview = timed("compose", lambda: build_step_preview(job))
        paths = timed("export", lambda: write_outputs(preview, job, str(output), per_layer_svg=True))
        artefacts.extend(Path(p) for p in paths.all_paths())
        source = Path(paths.json)
    else:
        source = next(p for p in (ROOT / "out" / case).glob("*.json")
                      if not p.name.endswith((".meta.json", ".MaterialMap.json")))
    record = {"case": case, "durations_s": durations}
    if args.mode in ("equivalent", "all"):
        document = json.loads(source.read_text(encoding="utf-8"))
        used = set()
        (output / "masks").mkdir(exist_ok=True)
        mask_time = 0.0

        def sink(name, mask, pitch):
            nonlocal mask_time
            start = time.perf_counter()
            path = output / "masks" / (unique_image_stem(name, used) + ".png")
            write_layer_mask(mask, str(path), pixel_step_mm=pitch)
            artefacts.append(path)
            mask_time += time.perf_counter() - start

        result = timed("equivalent_and_masks", lambda: equivalence_from_document(
            document, EquivalenceOptions(GridOptions(1, 1, mesh_preset="standard", auto_raster=True)),
            mask_sink=sink))
        durations["masks"] = mask_time
        durations["equivalent"] = durations.pop("equivalent_and_masks") - mask_time
        csv = output / (source.stem + ".MaterialMap.json")
        timed("csv", lambda: write_materialmap(result, str(csv)))
        meta = output / "diagnostics" / "run.json"
        meta.parent.mkdir(exist_ok=True)
        from odbpp import equiv as equiv_module
        if hasattr(equiv_module, "write_run_report"):
            equiv_module.write_run_report(result, str(meta), source_path=str(source))
        else:
            # A historical isolated baseline owns its former diagnostics layout.
            meta.write_text(json.dumps(result.metadata(),indent=1)+"\n",encoding="utf-8")
        images = timed("heatmaps", lambda: render_heatmaps(str(csv), str(output / "heatmaps")))
        artefacts.extend([csv, meta] + [Path(image.path) for image in images.images])
        record.update(fatal=result.fatal, regions=len(result.rectification.rectangles),
                      cells=result.cell_count, raster=[result.raster_px, result.raster_py])
    record["peak_memory_bytes"] = peak_memory_bytes()
    record["files"] = {p.relative_to(output).as_posix(): {
        "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(artefacts)}
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--mode", choices=("all", "preview", "equivalent"), default="all")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--source-root")
    parser.add_argument("--source-ref", help="save and benchmark src/ from this Git revision")
    parser.add_argument("--compare")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.source_ref and args.source_root:
        parser.error("--source-ref and --source-root are mutually exclusive")
    if args.worker:
        if args.profile:
            profiler = cProfile.Profile()
            record = profiler.runcall(worker, args)
            profiler.dump_stats(str(Path(args.output) / args.case[0] / "pipeline.prof"))
        else:
            record = worker(args)
        print(json.dumps(record, ensure_ascii=False))
        return
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.source_ref:
        args.source_root = snapshot_source(args.source_ref, output/"source")
    baseline = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else None
    report = {"mode": args.mode, "profiled": args.profile, "cases": {}}
    for case in args.case or CASES:
        runs = []
        for _ in range(args.repeat):
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--case", case,
                       "--output", str(output), "--mode", args.mode]
            if args.source_root:
                command += ["--source-root", args.source_root]
            if args.profile:
                command.append("--profile")
            completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8")
            record = json.loads(completed.stdout)
            if runs and record["files"] != runs[0]["files"]:
                raise RuntimeError(f"non-deterministic artefacts: {case}")
            runs.append(record)
        summary = {"runs": runs, "median_s": {
            stage: statistics.median(r["durations_s"][stage] for r in runs)
            for stage in runs[0]["durations_s"]}}
        report["cases"][case] = summary
        if baseline and runs[0]["files"] != baseline["cases"][case]["runs"][0]["files"]:
            raise RuntimeError(f"artefact mismatch: {case}")
        (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(case, json.dumps(summary["median_s"]), flush=True)


if __name__ == "__main__":
    main()
