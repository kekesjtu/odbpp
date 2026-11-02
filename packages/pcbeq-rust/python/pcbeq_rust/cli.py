"""Native JSON -> MaterialMap execution, followed by optional Python drawings."""
import argparse
from pathlib import Path
import sys

from . import __version__
from .native import run
from pcbeq.errors import EquivalenceErrorBase


def build_parser():
    parser = argparse.ArgumentParser(prog="pcbeq-rust", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pcbeq-rust {__version__}")
    command = parser.add_subparsers(dest="command", required=True).add_parser("run")
    command.add_argument("path")
    command.add_argument("-o", "--out")
    command.add_argument("--px", type=int)
    command.add_argument("--py", type=int)
    mesh = command.add_mutually_exclusive_group()
    mesh.add_argument("--grid-level", choices=("coarse", "standard", "fine"))
    mesh.add_argument("--max-cell-mm", type=float, nargs="+")
    command.add_argument("--max-boundary-retreat-mm", type=float, default=3.)
    command.add_argument("--max-area-loss-ratio", type=float, default=.05)
    command.add_argument("--diagnostic-geometry-step-mm", type=float, help=argparse.SUPPRESS)
    command.add_argument("--materials")
    for phase in ("copper", "base"):
        for key in ("k", "density", "cp"):
            command.add_argument(f"--{phase}-{key}", type=float)
    for name in ("heatmap", "regions", "grids", "gallery"):
        command.add_argument(f"--no-{name}", dest=name, action="store_false", default=True)
    command.add_argument("--heatmap-panel-width", type=int, default=1440)
    command.add_argument("--heatmap-shared-scale", action="store_true")
    command.add_argument("--heatmap-grid", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    output = Path(args.out or Path("out") / Path(args.path).stem)
    overrides = {}
    for phase in ("copper", "base"):
        fields = {field: getattr(args, f"{phase}_{key}") for key, field in
                  (("k", "k_w_mk"), ("density", "density_kg_m3"), ("cp", "specific_heat_j_kgk"))
                  if getattr(args, f"{phase}_{key}") is not None}
        if fields:
            overrides[phase] = fields
    try:
        result = run(args.path, output, px=args.px, py=args.py,
                     grid_level=args.grid_level or ("standard" if args.max_cell_mm is None else None),
                     max_cell_mm=args.max_cell_mm or [5, 5],
                     max_boundary_retreat_mm=args.max_boundary_retreat_mm,
                     max_area_loss_ratio=args.max_area_loss_ratio,
                     tolerance_mm=args.diagnostic_geometry_step_mm,
                     materials={"global": overrides} if overrides else {}, materials_path=args.materials)
        print(f"wrote {output / 'materialmap.json'}: {result['cells']} cells, "
              f"{result['regions']} regions; native {result['durations_s']['total']:.6f} s")
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        if any((args.heatmap, args.regions, args.grids, args.gallery)):
            from pcbeq.map_images import render
            render(output / "materialmap.json", output, heatmap=args.heatmap,
                   regions=args.regions, grids=args.grids, gallery=args.gallery,
                   panel_width=args.heatmap_panel_width, shared_scale=args.heatmap_shared_scale,
                   draw_grid=args.heatmap_grid)
        return 0
    except (OSError, ValueError, RuntimeError, EquivalenceErrorBase) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
