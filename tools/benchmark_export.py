"""Time complete exports and fingerprint every artifact for regression checks.

Run from the repository root in the odbpp conda environment. Profiling is
optional because cProfile substantially distorts Python-heavy timings.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import platform
import time

from odbpp import load_job, build_step_preview, write_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--per-layer-svg", action="store_true")
    parser.add_argument("--compare", type=Path,
                        help="Baseline benchmark.json; fail if artifact bytes differ")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = (json.loads(args.compare.read_text(encoding="utf-8"))
                if args.compare else None)
    if baseline and baseline["per_layer_svg"] != args.per_layer_svg:
        parser.error("--per-layer-svg must match the baseline")
    cases = args.cases or ["minimal-test-odb", "kicad-kitchen-sink",
                           "kicad-video-odb.tgz", "designodb-rigidflex.tgz"]
    report = {"python": platform.python_version(), "profiled": args.profile,
              "per_layer_svg": args.per_layer_svg, "cases": {}}
    for name in cases:
        target = args.output / name.removesuffix(".tgz")
        profiler = cProfile.Profile() if args.profile else None
        if profiler:
            profiler.enable()
        start = time.perf_counter()
        job = load_job(str(Path("cases") / name))
        loaded = time.perf_counter()
        preview = build_step_preview(job)
        composed = time.perf_counter()
        outputs = write_outputs(preview, job, str(target),
                                per_layer_svg=args.per_layer_svg)
        written = time.perf_counter()
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(target / "export.prof"))
        files = {}
        for filename in outputs.all_paths():
            path = Path(filename)
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            files[path.relative_to(target).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": digest.hexdigest()}
        result = {"load_s": loaded - start, "compose_s": composed - loaded,
                  "write_s": written - composed, "total_s": written - start,
                  "layers": len(preview.layers), "rings": preview.total_polygons,
                  "files": files}
        if baseline is not None:
            result["identical_artifacts"] = files == baseline["cases"][name]["files"]
        report["cases"][name] = result
        (args.output / "benchmark.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
        print(name, {k: round(v, 3) for k, v in result.items() if k.endswith("_s")},
              flush=True)
        del preview, job
    if baseline is not None and not all(
            case["identical_artifacts"] for case in report["cases"].values()):
        raise SystemExit("Export artifacts differ from the baseline")


if __name__ == "__main__":
    main()
