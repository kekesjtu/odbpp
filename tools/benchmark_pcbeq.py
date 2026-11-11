"""Benchmark intermediate JSON reading through closed MaterialMap output, without images.

Example (from the repository root):
    uv run python tools/benchmark_pcbeq.py out/minimal-test-odb/rust/pcbkit/intermediate.json         --output tmp/2026-11-02-pcbeq-benchmark/rust --repeat 3
"""
from __future__ import annotations
import argparse
import gc
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, help='existing intermediate JSON; no ODB++ parsing')
    parser.add_argument('--output', type=Path, required=True, help='benchmark.json and the final materialmap.json directory')
    parser.add_argument('--backend', choices=('rust',), default='rust')
    parser.add_argument('--repeat', type=int, default=3, help='measured runs (default: 3)')
    parser.add_argument('--warmup', type=int, default=1, help='unmeasured runs (default: 1)')
    parser.add_argument('--px', type=int)
    parser.add_argument('--py', type=int)
    parser.add_argument('--max-area-error-ratio', type=float, default=0.05)
    mesh = parser.add_mutually_exclusive_group()
    mesh.add_argument('--grid-level', choices=('coarse', 'standard', 'fine'))
    mesh.add_argument('--max-cell-mm', type=float, nargs='+', metavar='MM')
    parser.add_argument('--materials', type=Path, help='production material configuration JSON')
    return parser

def measure_native(source: Path, output: Path, settings: dict, materials_path: Path | None) -> dict:
    from pcbeq_rust.native import run
    gc.collect()
    cpu_start = time.process_time()
    result = run(source, output, **settings, materials_path=str(materials_path) if materials_path else None)
    result['cpu_s'] = time.process_time() - cpu_start
    return result

def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.warmup < 0:
        parser.error('repeat must be positive and warmup must be nonnegative')
    if (args.px is None) != (args.py is None):
        parser.error('--px and --py must be provided together')
    if args.input.resolve() in {args.output.resolve() / name for name in ('materialmap.json', 'materialmap.json.tmp', 'benchmark.json')}:
        parser.error('input must not be overwritten by benchmark outputs')
    errors = (OSError, ValueError, ImportError, RuntimeError)
    try:
        settings = {'px': args.px, 'py': args.py, 'grid_level': args.grid_level or ('standard' if args.max_cell_mm is None else None), 'max_cell_mm': args.max_cell_mm if args.max_cell_mm is not None else [5, 5], 'max_area_error_ratio': args.max_area_error_ratio}
        args.output.mkdir(parents=True, exist_ok=True)
        report = {'measurement': 'intermediate JSON read/validation through MaterialMap close and rename', 'excluded': ['ODB++ parsing', 'process startup/imports', 'images/HTML', 'benchmark metadata', 'fsync to physical storage'], 'cache_policy': 'same process; normal OS file cache; fresh document/options per run', 'environment': {'python': sys.version, 'platform': platform.platform(), 'processor': platform.processor(), 'cpu_count': os.cpu_count()}, 'backend': args.backend, 'input': {'path': str(args.input.resolve()), 'bytes': args.input.stat().st_size}, 'options': {**settings, 'materials_path': str(args.materials) if args.materials else None}, 'warmup': args.warmup, 'repeat': args.repeat, 'runs': []}
        for index in range(args.warmup + args.repeat):
            run = measure_native(args.input, args.output, settings, args.materials)
            warmup = index < args.warmup
            if not warmup:
                report['runs'].append(run)
            print(f"{('warmup' if warmup else 'run')} {index + 1}: {run['durations_s']['total']:.6f} s, {run['cells']} cells", flush=True)
        report['summary_s'] = {stage: {'median': statistics.median(values), 'min': min(values), 'max': max(values)} for stage in ('read_json', 'equivalence_and_write', 'total') for values in [[run['durations_s'][stage] for run in report['runs']]]}
        (args.output / 'benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for warning in report['runs'][-1]['warnings']:
            print(warning, file=sys.stderr)
        print(f"median: {report['summary_s']['total']['median']:.6f} s; report: {args.output / 'benchmark.json'}")
        return 0
    except errors as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
