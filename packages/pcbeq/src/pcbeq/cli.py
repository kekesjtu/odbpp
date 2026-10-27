from __future__ import annotations
import argparse
import json
import sys
from typing import Optional, Sequence
from . import __version__
from .diagnostics import MATERIALS_DEFAULT_USED, VISUALIZE_FAILED, Diagnostics

from .application import run

def build_parser():
    parser = argparse.ArgumentParser(prog="pcbeq", description="PCB material equivalence from intermediate JSON only")
    parser.add_argument("--version", action="version", version=f"pcbeq {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p_eq = sub.add_parser(
        "run",
        help="material equivalence: preview JSON -> MaterialMap.json",
        description="Equivalent each copper and dielectric layer to per-cell "
                    "kx/ky/kz, density and specific heat.",
    )
    p_eq.add_argument("path", help="Intermediate JSON file (millimetres, positive polygons)")
    p_eq.add_argument("-o", "--out", default=None,
                      help="output directory (default: out/<job name>)")
    p_eq.add_argument("--px", type=int, default=None,
                      help="X raster pixels (default: dense geometry-aware sampling)")
    p_eq.add_argument("--py", type=int, default=None,
                      help="Y raster pixels (provide together with --px)")
    p_eq.add_argument("--grid-level", choices=("coarse","standard","fine"), default=None,
                      help="adaptive mesh preset (default: standard; expert sizes override)")
    p_eq.add_argument("--min-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                      help="minimum cell edge, one value or X Y (default 0.1 mm)")
    p_eq.add_argument("--max-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                      help="maximum cell edge, one value or X Y (default 5 mm)")
    p_eq.add_argument("--diagnostic-geometry-step-mm", dest="rectify_tolerance",
                      type=float, default=None, help=argparse.SUPPRESS)
    p_eq.add_argument("--max-boundary-retreat-mm", type=float, default=3.0, metavar="MM",
                      help="maximum full-contour inward approximation distance (default: 3 mm)")
    p_eq.add_argument("--max-area-loss-ratio", dest="max_area_error_ratio",
                      type=float, default=0.05, metavar="RATIO",
                      help="maximum lost area / original effective domain area, in [0,1) (default: 0.05)")
    p_eq.add_argument("--materials", default=None, metavar="FILE",
                      help="JSON material configuration (global and per-layer "
                           "overrides)")
    p_eq.add_argument("--copper-k", type=float, default=None, metavar="W/MK")
    p_eq.add_argument("--copper-density", type=float, default=None, metavar="KG/M3")
    p_eq.add_argument("--copper-cp", type=float, default=None, metavar="J/KG/K")
    p_eq.add_argument("--base-k", type=float, default=None, metavar="W/MK")
    p_eq.add_argument("--base-density", type=float, default=None, metavar="KG/M3")
    p_eq.add_argument("--base-cp", type=float, default=None, metavar="J/KG/K")
    p_eq.add_argument("--no-raster-png", dest="raster_png",
                      action="store_false", default=True,
                      help="do not write the per-layer black/white raster PNGs "
                           "under <out>/masks/")
    p_eq.add_argument("--raster-scale", type=int, default=0, metavar="N",
                      help="isotropic nearest-neighbour zoom for the mask PNGs, "
                           "stretching a non-square board (default 0 = auto: "
                           "about 1400 px on the long side, board proportions "
                           "kept; 1 = exactly px x py)")
    p_eq.add_argument("--no-heatmap", dest="heatmap", action="store_false",
                      default=True,
                      help="do not write the per-layer kx/ky/kz heat maps "
                           "under <out>/heatmaps/")
    p_eq.add_argument("--heatmap-panel-width", type=int, default=1440,
                      metavar="PX",
                      help="width of one heat-map panel in pixels (default 1440; "
                           "height follows the board aspect ratio)")
    p_eq.add_argument("--heatmap-shared-scale", action="store_true",
                      help="use one colour scale for every layer instead of one "
                           "per layer")
    p_eq.add_argument("--heatmap-grid", action="store_true",
                      help="outline the JSON cells in the heat maps")
    p_eq.add_argument("--no-gallery", dest="gallery", action="store_false",
                      default=True,
                      help="do not write the per-run HTML gallery")
    p_eq.set_defaults(json_out=None, format=None, max_records=5)

    p_eq.add_argument("--no-regions", dest="regions", action="store_false", default=True)
    p_eq.add_argument("--no-grids", dest="grids", action="store_false", default=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(build_parser().parse_args(argv), Diagnostics())
