from __future__ import annotations
import argparse
import json
import sys
from typing import Any, Optional, Sequence
from . import __version__
from .diagnostics import Diagnostics
from .errors import OdbError
from .geometry import DEFAULT_SAGITTA_MM, SUPPORTED_SYMBOL_PREFIXES, TessellationOptions
from .job import LoadOptions, load_job
from .model import Job, Layer, Step
from .symbols import parse_standard_symbol

from .application import (summarize, _run_export, _print_info, _print_layers,
    _print_symbols, _print_diagnostics, _print_summary)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcbkit",
        description="Inspect an ODB++ v8.1 job (directory, .tgz or .zip).",
    )
    parser.add_argument("--version", action="version", version=f"pcbkit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", help="ODB++ directory, .tgz/.tar.gz or .zip")
        p.add_argument("--step", default=None,
                       help="step name to load (default: every step in the matrix)")
        p.add_argument("--no-features", action="store_true",
                       help="inventory only; skip parsing features files")

    p_info = sub.add_parser("info", help="job and matrix summary")
    common(p_info)

    p_layers = sub.add_parser("layers", help="layer inventory")
    common(p_layers)

    p_symbols = sub.add_parser("symbols", help="symbol inventory")
    common(p_symbols)

    p_diag = sub.add_parser("diagnostics", help="warnings and errors")
    common(p_diag)

    p_sum = sub.add_parser("summarize", help="everything at once")
    common(p_sum)
    p_sum.add_argument("--format", choices=("text", "json"), default="text")
    p_sum.add_argument("--json", dest="json_out", default=None,
                       help="write the JSON summary to this file")
    p_sum.add_argument("--max-records", type=int, default=5,
                       help="example feature records to show per layer (text only)")

    p_exp = sub.add_parser("export", help="compose layers; write JSON/SVG/HTML preview",)
    common(p_exp)
    p_exp.add_argument("-o", "--out", default=None,
                       help="output directory (default: out/<job name>)")
    p_exp.add_argument("--layer", action="append", default=None, metavar="NAME",
                       help="restrict to this layer; repeatable")
    p_exp.add_argument("--exclude-type", action="append", default=None,
                       metavar="TYPE",
                       help="skip layers of this matrix TYPE (e.g. DOCUMENT); repeatable")
    p_exp.add_argument("--sagitta-mm", "--sagitta", dest="sagitta", type=float, default=DEFAULT_SAGITTA_MM,
                       help=f"curve flattening tolerance in mm (default {DEFAULT_SAGITTA_MM})")
    p_exp.add_argument("--digits", type=int, default=4,
                       help="decimal places for coordinates (default 4, i.e. 0.1 um)")
    p_exp.add_argument("--minimum-stroke", type=float, default=0.0, metavar="MM",
                       help="draw zero-width artwork (r0 lines) at this width "
                            "(default 0, which keeps the geometry exact and "
                            "therefore invisible)")
    p_exp.add_argument("--only-json", action="store_true",
                       help="write only the intermediate JSON")
    p_exp.add_argument("--per-layer-svg", action="store_true",
                       help="also write one SVG per layer under <out>/layers/")
    p_exp.add_argument("--no-diagnostics", action="store_true",
                       help="omit the final parser diagnostic listing on stderr; exit status is unchanged")
    p_exp.set_defaults(json_out=None, format=None, max_records=5, no_features=False)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    import time
    started = time.perf_counter()
    args = build_parser().parse_args(argv)
    args._started = started

    if args.command == "export":
        import math
        if (not math.isfinite(args.sagitta) or args.sagitta <= 0 or args.digits < 0
                or not math.isfinite(args.minimum_stroke) or args.minimum_stroke < 0):
            print("error: precision must be finite; sagitta > 0, digits >= 0, minimum stroke >= 0", file=sys.stderr)
            return 2

    diagnostics = Diagnostics()

    options = LoadOptions(step=args.step, load_features=not args.no_features)

    try:
        job = load_job(args.path, options=options, diagnostics=diagnostics)
    except OdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "export":
        return _run_export(job, args, diagnostics)

    if args.command == "info":
        _print_info(job)
    elif args.command == "layers":
        _print_layers(job)
    elif args.command == "symbols":
        _print_symbols(job)
    elif args.command == "diagnostics":
        _print_diagnostics(job)
    else:
        summary = summarize(job)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        if args.json_out is None or args.format == "text":
            if args.format == "json":
                json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
                sys.stdout.write("\n")
            else:
                _print_summary(job, summary, max_records=args.max_records)
        elif args.json_out:
            print(f"wrote {args.json_out}")

    # Warnings are part of the result: a caller scripting this should be able to
    # tell "clean" from "loaded, but something was dropped" without parsing prose.
    return 1 if diagnostics.errors else 0
