"""Run the pcbeq process pipeline or draw an existing MaterialMap."""
import argparse
from pathlib import Path
import sys
from textwrap import dedent

from . import __version__
from .errors import EquivalenceErrorBase
from .runner import run_command


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pcbeq-rust",
        description="Calculate equivalent PCB properties with the standalone pcbeq executable\n"
                    "and render engineering plots from its MaterialMap.",
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples
              pcbeq-rust run intermediate.json -o result --grid-level standard
              pcbeq-rust plot result/materialmap.json

            Run 'pcbeq-rust run --help' or 'pcbeq-rust plot --help' for options.
            """),
    )
    parser.add_argument("--version", action="version", version=f"pcbeq-rust {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, title="commands", metavar="COMMAND")
    run = commands.add_parser(
        "run",
        help="Compute a MaterialMap with pcbeq and render plots",
        description="Run pcbeq on an intermediate board JSON and write materialmap.json,\n"
                    "diagnostics.json, and the selected plots to the output directory.\n"
                    "A plotting failure returns 2 and preserves published numerical results.",
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Calculation options forwarded to pcbeq
              --grid-level {coarse,standard,fine} or --max-cell-mm MM [MM]
                Choose a grid preset or set the maximum cell size in X and Y.
              --max-area-error-ratio RATIO   Relative area error limit per profile.
              --pixels-per-cell-axis N       Sample N by N pixels per cell (default: 9).
              --mode {1,2}                  1 for 2DVTN, 2 for SPTRA.
              --materials FILE, --materials-json JSON
                Load material properties from a file or pass inline JSON.
              --copper-k, --copper-density, --copper-cp
              --base-k, --base-density, --base-cp
                Set global conductivity, density, or specific heat by phase.

            Run 'pcbeq run --help' for calculation units, defaults, and constraints.
            The wrapper requests the JSON summary internally.

            Examples
              pcbeq-rust run intermediate.json -o result --max-cell-mm 2 3 --mode 2
              pcbeq-rust run intermediate.json --no-heatmap --no-regions --no-grids --no-gallery
            """),
    )
    run.add_argument("path", metavar="INTERMEDIATE_JSON",
                     help="Intermediate board JSON exported by pcbkit-rust.")
    run.add_argument("-o", "--out", metavar="DIR",
                     help="Output directory for numerical results and plots "
                          "(default out/<input file stem> relative to the current directory).")
    run.add_argument("--pcbeq-exe", metavar="EXE",
                     help="Path to the pcbeq executable. If omitted, use PCBEQ_EXE, "
                          "then look for pcbeq on PATH.")
    plot = commands.add_parser(
        "plot",
        help="Render engineering plots from an existing MaterialMap",
        description="Draw conductivity heatmaps, region outlines, and local grids from a\n"
                    "published MaterialMap. Only the MaterialMap is required, so plots can be\n"
                    "regenerated without the board input or the pcbeq executable.",
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples
              pcbeq-rust plot result/materialmap.json --heatmap-shared-scale --heatmap-grid
              pcbeq-rust plot result/materialmap.json -o plots --no-heatmap
            """),
    )
    plot.add_argument("path", metavar="MATERIALMAP_JSON",
                      help="MaterialMap JSON written by pcbeq, usually materialmap.json.")
    plot.add_argument("-o", "--out", metavar="DIR",
                      help="Output directory for plots (default the MaterialMap's directory).")
    for command in (run, plot):
        plots = command.add_argument_group(
            "plot options", "All plot types and the HTML gallery are enabled by default."
        )
        for name, description in (
            ("heatmap", "Skip per-layer kx, ky, and kz heatmaps in heatmaps/*.png."),
            ("regions", "Skip region outlines in regions/regions.png and .svg."),
            ("grids", "Skip cell grid plots in grids/cartesian-grid.png and .svg."),
            ("gallery", "Skip the HTML plot gallery (index.html)."),
        ):
            plots.add_argument(f"--no-{name}", dest=name, action="store_false",
                               default=True, help=description)
        plots.add_argument("--heatmap-panel-width", type=int, default=1440, metavar="PX",
                           help="Target width per conductivity heatmap panel in pixels "
                                "(32 to 2400, default %(default)s).")
        plots.add_argument("--heatmap-shared-scale", action="store_true",
                           help="Use one conductivity color scale across all layers. "
                                "By default, each layer shares a scale across its three directions.")
        plots.add_argument("--heatmap-grid", action="store_true",
                           help="Overlay the actual cell boundaries on the conductivity heatmaps.")
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
