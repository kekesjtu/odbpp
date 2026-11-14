"""Run the pcbeq process pipeline or draw an existing MaterialMap."""
import argparse
from pathlib import Path
import sys

from . import __version__
from .errors import EquivalenceErrorBase
from .runner import run_command


def build_parser():
    parser = argparse.ArgumentParser(prog="pcbeq-rust", description=__doc__, allow_abbrev=False)
    parser.add_argument("--version", action="version", version=f"pcbeq-rust {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False, description="Compute and draw. Calculation options are forwarded to pcbeq; see pcbeq run --help.")
    run.add_argument("path")
    run.add_argument("-o", "--out")
    run.add_argument("--pcbeq-exe", help="pcbeq executable; defaults to PCBEQ_EXE or PATH")
    plot = commands.add_parser("plot", allow_abbrev=False, help="draw an existing MaterialMap without calculation")
    plot.add_argument("path")
    plot.add_argument("-o", "--out")
    for command in (run, plot):
        for name in ("heatmap", "regions", "grids", "gallery"):
            command.add_argument(f"--no-{name}", dest=name, action="store_false", default=True)
        command.add_argument("--heatmap-panel-width", type=int, default=1440)
        command.add_argument("--heatmap-shared-scale", action="store_true")
        command.add_argument("--heatmap-grid", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args, calculation = parser.parse_known_args(argv)
    if args.command == "plot" and calculation:
        parser.error("unrecognized arguments: " + " ".join(calculation))
    output = Path(args.out or (Path(args.path).parent if args.command == "plot" else Path("out") / Path(args.path).stem))
    try:
        source = Path(args.path)
        if args.command == "run":
            result = run_command(source, output, calculation, program=args.pcbeq_exe)
            source = output / "materialmap.json"
            print(f"wrote {source}: {result['cells']} cells, {result['regions']} regions; compute {result['performance']['total_s']:.6f} s")
            for kind, label in (("warnings", "warning"), ("errors", "error")):
                for issue in result["issues"][kind]:
                    print(f"{label}: {issue['code']}: {issue['message']} (layers: {issue.get('layers', [])})", file=sys.stderr)
        if any((args.heatmap, args.regions, args.grids, args.gallery)):
            from .map_images import render
            render(source, output, heatmap=args.heatmap, regions=args.regions,
                   grids=args.grids, gallery=args.gallery,
                   panel_width=args.heatmap_panel_width, shared_scale=args.heatmap_shared_scale,
                   draw_grid=args.heatmap_grid)
        return 0
    except (OSError, ValueError, RuntimeError, EquivalenceErrorBase) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
