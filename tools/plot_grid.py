"""Draw the actual global Cartesian axes from a final MaterialMap metadata file.

uv run --all-extras python tools/plot_grid.py out/<case>/<step>.json
Writes grid/cartesian-grid.png, .svg and .stats.json. No geometry is replanned.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def plot_grid(document: dict, directory: Path, metadata: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    from matplotlib.path import Path as VectorPath
    from matplotlib.patches import PathPatch, Rectangle

    grid = metadata["grid"]
    if grid.get("layout") != "global-cartesian" or not metadata["rectification"]["ok"]:
        raise ValueError("refusing to illustrate invalid or non-Cartesian geometry")
    xs, ys = grid["x_lines_mm"], grid["y_lines_mm"]
    if any(not math.isfinite(v) for values in (xs, ys) for v in values):
        raise ValueError("grid axes must be finite")
    if any(b <= a for values in (xs, ys) for a, b in zip(values, values[1:])):
        raise ValueError("grid axes must be strictly increasing")
    if not grid["cells"]:
        raise ValueError("no modeled grid cells")
    for cell in grid["cells"]:
        i, j = cell["i"], cell["j"]
        expected = (xs[i], ys[j], xs[i+1], ys[j+1])
        actual = tuple(cell[k] for k in ("x0_mm", "y0_mm", "x1_mm", "y1_mm"))
        if any(abs(a-b) > 1e-8 for a, b in zip(actual, expected)):
            raise ValueError(f"cell ({i},{j}) does not follow final global axes")

    step = document["steps"][0]
    bounds = metadata["raster"]["board_bounds_mm"]
    x0, y0, x1, y1 = bounds
    rects = metadata["rectification"]["rectangles"]
    segments = [[(x, y0), (x, y1)] for x in xs] + [[(x0, y), (x1, y)] for y in ys]
    vertices, codes = [], []
    for r in rects:
        a,b,c,d = (r[k] for k in ("x0_mm", "y0_mm", "x1_mm", "y1_mm"))
        vertices.extend([(a,b),(c,b),(c,d),(a,d),(a,b)])
        codes.extend([VectorPath.MOVETO, VectorPath.LINETO, VectorPath.LINETO,
                      VectorPath.LINETO, VectorPath.CLOSEPOLY])
    model_path = VectorPath(vertices, codes)
    original = next(d for d in step["domains"] if d["id"] == step["profile_domain_id"])
    width, height = x1-x0, y1-y0
    # Focus at an actual modeled cell, so the inset cannot accidentally be outside the board.
    central = min(grid["cells"], key=lambda c: ((c["x0_mm"]+c["x1_mm"]-x0-x1)/width)**2
                   + ((c["y0_mm"]+c["y1_mm"]-y0-y1)/height)**2)
    cx=(central["x0_mm"]+central["x1_mm"])/2
    cy=(central["y0_mm"]+central["y1_mm"])/2
    edge = min(grid["cells"], key=lambda c: c["x0_mm"])
    ex=(edge["x0_mm"]+edge["x1_mm"])/2
    ey=(edge["y0_mm"]+edge["y1_mm"])/2

    fig = plt.figure(figsize=(16, 10), layout="constrained")
    layout = fig.add_gridspec(2,3,height_ratios=(1.4,1))
    overview = fig.add_subplot(layout[0,:2])
    left = fig.add_subplot(layout[1,0])
    centre = fig.add_subplot(layout[1,1])
    info = fig.add_subplot(layout[:,2]); info.axis("off")
    panels = [(overview,bounds,"All global axes (actual mesh)"),
              (left,(max(x0,ex-width/16),max(y0,ey-height/8),
                     min(x1,ex+width/16),min(y1,ey+height/8)),"Boundary detail"),
              (centre,(max(x0,cx-width/16),max(y0,cy-height/8),
                       min(x1,cx+width/16),min(y1,cy+height/8)),"Interior detail")]
    for ax, window, title in panels:
        ax.set_facecolor("#edf0f4")
        fill=PathPatch(model_path,facecolor="white",edgecolor="none")
        ax.add_patch(fill)
        # Light extensions show the full global lattice; clipped blue lines are modeled cell borders.
        ax.add_collection(LineCollection(segments,colors="#a6b0bc",linewidths=.3,
                                         linestyles="dotted",alpha=.6))
        real=LineCollection(segments,colors="#307bb3",linewidths=.35,alpha=.85)
        real.set_clip_path(fill)
        real.set_clip_box(ax.bbox)
        ax.add_collection(real)
        for r in rects:
            a,b,c,d = (r[k] for k in ("x0_mm", "y0_mm", "x1_mm", "y1_mm"))
            ax.add_patch(Rectangle((a,b),c-a,d-b,fill=False,edgecolor="#d28a26",linewidth=.6))
        for polygon in original["polygons"]:
            points=polygon["points"]+polygon["points"][:1]
            ax.plot([p[0] for p in points],[p[1] for p in points],color="#ce3645",
                    linewidth=.8,linestyle="--")
        ax.set_xlim(window[0],window[2]); ax.set_ylim(window[1],window[3])
        ax.set_aspect("equal"); ax.set_title(title)
        ax.set_xlabel("X / mm"); ax.set_ylabel("Y / mm")

    constraints=grid["size_constraints_mm"]
    config=grid.get("mesh_configuration") or {}
    mode=config.get("preset") or config.get("mode","expert")
    info.text(0,1,
        f"GLOBAL CARTESIAN MESH\n\n"
        f"Global shape: {len(xs)-1} x {len(ys)-1}\n"
        f"Axes: X={len(xs)}, Y={len(ys)}\n"
        f"Modeled XY cells: {len(grid['cells']):,}\n"
        f"Regions: {len(rects)}\n"
        f"Sizing: {mode}\n\n"
        f"Min X/Y: {constraints['min'][0]:.4g} / {constraints['min'][1]:.4g} mm\n"
        f"Max X/Y: {constraints['max'][0]:.4g} / {constraints['max'][1]:.4g} mm\n\n"
        "Every region boundary is retained.\n"
        "Axis coordinates propagate across\nthe whole board.\n\n"
        "White = modeled XY footprint\n"
        "Grey = no modeled cells\n"
        "Layer presence is determined\nseparately for every physical layer.\n\n"
        "Geometry only: no raster pixels\nor material values are shown.\n"
        "Axes are read from final metadata;\nno re-meshing or simplification.",
        va="top",fontsize=11,linespacing=1.45)
    info.legend(handles=[Line2D([],[],color="#307bb3",label="Actual cell boundaries"),
                         Line2D([],[],color="#a6b0bc",linestyle=":",label="Global axes outside model"),
                         Line2D([],[],color="#d28a26",label="Region boundaries"),
                         Line2D([],[],color="#ce3645",linestyle="--",label="Original step profile")],
                loc="lower left",fontsize=10)
    fig.suptitle(f"{step['name']} | Global Cartesian grid (not a material heatmap)",fontsize=16)
    directory.mkdir(parents=True,exist_ok=True)
    fig.savefig(directory/"cartesian-grid.png",dpi=180)
    fig.savefig(directory/"cartesian-grid.svg")
    plt.close(fig)
    stats={"schema":"odbpp/grid-illustration-1","source":"MaterialMap.json",
           "layout":grid["layout"],"nx":len(xs)-1,"ny":len(ys)-1,
           "region_count":len(rects),"cell_count":len(grid["cells"]),
           "x_lines_mm":xs,"y_lines_mm":ys,"board_bounds_mm":bounds,
           "size_constraints_mm":constraints,"cell_axis_alignment_checked":True,
           "scope":"shared XY geometry; per-layer existence remains in MaterialMap metadata"}
    (directory/"cartesian-grid.stats.json").write_text(json.dumps(stats,indent=2),encoding="utf-8")
    return stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json",type=Path)
    parser.add_argument("--metadata",type=Path)
    parser.add_argument("-o","--out",type=Path)
    args=parser.parse_args()
    metadata=args.metadata or args.json.parent/"diagnostics"/"run.json"
    from odbpp.materialmap import read_document, restore_report_grid
    report=restore_report_grid(json.loads(metadata.read_text(encoding="utf-8")),
                               read_document(args.json.with_name(args.json.stem+".MaterialMap.json")))
    stats=plot_grid(json.loads(args.json.read_text(encoding="utf-8")),
                    args.out or args.json.parent/"grid",
                    report)
    print(f"global Cartesian grid: {stats['nx']} x {stats['ny']}; {stats['cell_count']} modeled XY cells")


if __name__=="__main__":
    main()
