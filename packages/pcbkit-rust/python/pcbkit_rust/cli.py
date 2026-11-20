from __future__ import annotations
import argparse
import json
import sys
from textwrap import dedent
from typing import Optional, Sequence
from . import __version__
from .diagnostics import Diagnostics
from .errors import OdbError
from .geometry import DEFAULT_SAGITTA_MM
from .job import LoadOptions, load_job

from .application import (summarize, _run_export, _print_info, _print_layers,
    _print_symbols, _print_diagnostics, _print_summary)

def build_parser(*, prog="pcbkit-rust") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Inspect ODB++ v8.1 jobs and export a single board step as intermediate JSON\n"
            "for pcbeq. Export also produces SVG and HTML previews by default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent(f"""\
            Examples
              {prog} layers board.tgz --no-features
              {prog} export board.tgz -o out/board --per-layer-png
              {prog} export --help
            """),
    )
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, title="commands", metavar="COMMAND")

    def common(p: argparse.ArgumentParser, *, step_help: str, features_help: str) -> None:
        p.add_argument("path", metavar="ODB_PATH",
                       help="ODB++ job directory or archive (.tgz, .tar.gz, .tar, or .zip).")
        p.add_argument("--step", metavar="NAME", help=step_help)
        p.add_argument("--no-features", action="store_true", help=features_help)

    def inspection(name: str, summary: str, description: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=summary, description=description)
        common(command,
               step_help="Load one named step (case-insensitive). By default, load all steps.",
               features_help="Skip feature files for a faster inventory. Feature counts then "
                             "show zero, and symbol references and feature diagnostics are omitted.")
        return command

    inspection("info", "Show job and matrix metadata",
               "Show the job root, units, product metadata, matrix counts, and orphan step directories.")
    inspection("layers", "List layers for each loaded step",
               "List layers in matrix order with context, type, polarity, feature count, and profile status.")
    inspection("symbols", "List standard and user-defined symbols referenced by layers",
               "List symbols referenced in layer feature files. Standard symbols include their "
               "parameters and native expansion support. Keep feature loading enabled for this command.")
    inspection("diagnostics", "Show warnings and errors detected while loading the job",
               "Report warnings and errors encountered while loading the job. Exit status 1 means "
               "the job loaded with recorded errors. A fatal load failure returns 2.")

    p_sum = inspection("summarize", "Combine job, step, layer, and diagnostic information",
                       "Write a text or JSON inspection report. The text report also includes "
                       "an inventory of referenced symbols.")
    p_sum.add_argument("--format", choices=("text", "json"), default="text",
                       help="Summary format (default %(default)s). JSON goes to stdout "
                            "unless --json FILE is supplied.")
    p_sum.add_argument("--json", dest="json_out", metavar="FILE",
                       help="Save the full JSON report to FILE. With the default text format, "
                            "also print the text report to stdout.")
    p_sum.add_argument("--max-records", type=int, default=5, metavar="N",
                       help="Show up to N example feature records per layer in the text report "
                            "(default %(default)s). Use 0 to omit examples.")

    p_exp = sub.add_parser(
        "export",
        help="Export one board step as intermediate JSON and optional previews",
        description=(
            "Compose a single board step and write intermediate.json in millimetres.\n"
            "By default, also write preview.svg and preview.html. Every successful export\n"
            "writes diagnostics/run.json and diagnostics/artifacts.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent(f"""\
            Examples
              {prog} export board.tgz -o out/board --per-layer-png
              {prog} export panel.zip --step board -o out/board --only-json
            """),
    )
    common(p_exp,
           step_help="Select the board step by name (case-insensitive). Required if the job "
                     "contains multiple steps.",
           features_help="Skip reading the artwork in feature files. Keep feature loading enabled "
                         "for a complete geometry export.")
    p_exp.add_argument("-o", "--out", metavar="DIR",
                       help="Output directory (default out/<input name>, including any archive extension).")

    geometry = p_exp.add_argument_group("geometry options")
    geometry.add_argument("--sagitta-mm", "--sagitta", dest="sagitta", type=float,
                          default=DEFAULT_SAGITTA_MM, metavar="MM",
                          help="Curve flattening tolerance in mm (default %(default)s). "
                               "Use a finite positive value. Smaller values produce finer curves.")
    geometry.add_argument("--digits", type=int, default=4, metavar="N",
                          help="Snap exported coordinates to N decimal places in mm. Use a "
                               "nonnegative integer (default %(default)s, a 0.1 micrometre grid).")
    geometry.add_argument("--minimum-stroke", type=float, default=0.0, metavar="MM",
                          help="Width to give zero-width artwork such as r0 lines. Use a finite "
                               "nonnegative value in mm (default %(default)s, leaves them at zero width).")

    previews = p_exp.add_argument_group("preview options")
    previews.add_argument("--only-json", action="store_true",
                          help="Export intermediate.json, run diagnostics, and the artifact list. "
                               "Skip all previews, including per-layer images.")
    previews.add_argument("--per-layer-svg", action="store_true",
                          help="Also write one SVG per layer under <out>/layers/.")
    previews.add_argument("--per-layer-png", action="store_true",
                          help="Also write one PNG per layer under <out>/layers/, using a common viewport and scale.")
    p_exp.set_defaults(json_out=None, format=None, max_records=5, no_features=False)

    return parser


def main(argv: Optional[Sequence[str]] = None, *, prog="pcbkit-rust") -> int:
    import time
    started = time.perf_counter()
    args = build_parser(prog=prog).parse_args(argv)
    args._started = started

    if args.command == "export":
        import math
        if (not math.isfinite(args.sagitta) or args.sagitta <= 0 or args.digits < 0
                or not math.isfinite(args.minimum_stroke) or args.minimum_stroke < 0):
            print("error: precision must be finite; sagitta > 0, digits >= 0, minimum stroke >= 0", file=sys.stderr)
            return 2

    diagnostics = Diagnostics()

    options = LoadOptions(step=args.step, load_features=not args.no_features,
                          load_misc=not getattr(args, "only_json", False))

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
