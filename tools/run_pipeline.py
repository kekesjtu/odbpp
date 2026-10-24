"""Run the independently installed PCB CLIs through their on-disk JSON interface."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def command(executable: str | None, name: str) -> str:
    resolved = shutil.which(executable or name)
    if resolved is None:
        raise ValueError(f"cannot find {name} executable: {executable or name}")
    return resolved


def run_stage(name: str, argv: list[str], directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    stdout, stderr = directory / f"{name}.stdout.log", directory / f"{name}.stderr.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        result = subprocess.run(argv, stdout=out, stderr=err, shell=False)
    record = {"name": name, "command": argv, "returncode": result.returncode,
              "elapsed_s": time.perf_counter() - start,
              "stdout": stdout.name, "stderr": stderr.name}
    print(f"{name}: exit={result.returncode}, {record['elapsed_s']:.3f}s", flush=True)
    if result.returncode:
        print(stderr.read_text(encoding="utf-8"), flush=True)
    return record


def execute(source: Path, out: Path, *, parser_args=(), equivalent_args=(),
            pcbkit_exe=None, pcbeq_exe=None, reuse_parser=False, reuse_equivalent=False) -> dict:
    """No imports from either product; only arguments, files and exit statuses."""
    source, out = source.resolve(), out.resolve()
    report_dir = out / "pipeline"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"source": str(source), "status": "failed", "stages": []}
    destination = report_dir / "run.json"
    intermediate = out / "pcbkit/intermediate.json"
    materialmap = out / "pcbeq/materialmap.json"
    try:
        if not reuse_parser:
            stage = run_stage("pcbkit", [command(pcbkit_exe, "pcbkit"), "export", str(source),
                "-o", str(out / "pcbkit"), *parser_args], report_dir)
            report["stages"].append(stage)
            if stage["returncode"]:
                return report
        if not intermediate.is_file():
            raise ValueError(f"pcbkit did not publish {intermediate}")
        report["intermediate_sha256"] = hashlib.sha256(intermediate.read_bytes()).hexdigest()
        if not reuse_equivalent:
            stage = run_stage("pcbeq", [command(pcbeq_exe, "pcbeq"), "run", str(intermediate),
                "-o", str(out / "pcbeq"), *equivalent_args], report_dir)
            report["stages"].append(stage)
            if stage["returncode"]:
                return report
        if not materialmap.is_file():
            raise ValueError(f"pcbeq did not publish {materialmap}")
        report["materialmap_sha256"] = hashlib.sha256(materialmap.read_bytes()).hexdigest()
        report["status"] = "success"
        return report
    except (OSError, ValueError) as exc:
        report["error"] = str(exc)
        return report
    finally:
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--pcbkit-exe")
    parser.add_argument("--pcbeq-exe")
    parser.add_argument("--step")
    parser.add_argument("--sagitta-mm", type=float)
    parser.add_argument("--digits", type=int)
    parser.add_argument("--px", type=int)
    parser.add_argument("--py", type=int)
    parser.add_argument("--grid-level", choices=("coarse", "standard", "fine"))
    parser.add_argument("--min-cell-mm", type=float, nargs="+")
    parser.add_argument("--max-cell-mm", type=float, nargs="+")
    parser.add_argument("--materials", type=Path)
    parser.add_argument("--heatmap-grid", action="store_true")
    return parser


def forwarded(args) -> tuple[list[str], list[str]]:
    groups = []
    for names in (("step", "sagitta_mm", "digits"),
                  ("px", "py", "grid_level", "min_cell_mm", "max_cell_mm", "materials", "heatmap_grid")):
        options = []
        for name in names:
            value = getattr(args, name, None)
            if value is None or value is False:
                continue
            options.append("--" + name.replace("_", "-"))
            if value is not True:
                options.extend(map(str, value if isinstance(value, list) else [value]))
        groups.append(options)
    return tuple(groups)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    parser_args, equivalent_args = forwarded(args)
    result = execute(args.source, args.out, parser_args=parser_args, equivalent_args=equivalent_args,
                     pcbkit_exe=args.pcbkit_exe, pcbeq_exe=args.pcbeq_exe)
    if result.get("error"):
        print(result["error"])
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
