"""Plot the actual outline rectangles, without adding equivalence grid lines.

Run: uv run --all-extras python tools/plot_regions.py out/<case>/<step>.json
     --px 400 --py 400
Outputs default to out/<case>/regions/. SVG tooltips and CSV identify all regions.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

from ..rectify import Rect


def plot_regions(document: dict, directory: Path, *, metadata: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    from matplotlib.patches import Rectangle
    from matplotlib.cm import ScalarMappable
    from matplotlib.ticker import MaxNLocator

    step = document["steps"][0]
    actual = metadata["rectification"]
    if not actual["ok"] or not actual["rectangles"]:
        raise ValueError("geometry failed; refusing to plot a valid-looking board")
    directory.mkdir(parents=True, exist_ok=True)
    rects = [Rect(r["x0_mm"],r["y0_mm"],r["x1_mm"],r["y1_mm"]) for r in actual["rectangles"]]
    areas = [r.area for r in rects]
    shortest = [min(r.width, r.height) for r in rects]
    bounds = tuple(metadata["raster"]["board_bounds_mm"]) if metadata else (min(r.x0 for r in rects), min(r.y0 for r in rects), max(r.x1 for r in rects), max(r.y1 for r in rects))
    x0, y0, x1, y1 = bounds
    records = [{"region_id": i, **r.as_dict(), "width_mm": r.width,
                "height_mm": r.height, "area_mm2": r.area}
               for i, r in enumerate(rects)]
    stats = {"step": step["name"], 
             "rectification": actual, "region_count": len(rects),
             "min_area_mm2": min(areas), "median_area_mm2": statistics.median(areas),
             "max_area_mm2": max(areas),
             "min_short_edge_mm": min(shortest),
             "short_edge_below_1mm_count": sum(v < 1 for v in shortest),
             "area_below_1mm2_count": sum(v < 1 for v in areas),
             "forced_x_lines": len({x for r in rects for x in (r.x0,r.x1)}),
             "forced_y_lines": len({y for r in rects for y in (r.y0,r.y1)})}
    stem = "regions"
    with (directory / f"{stem}.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    (directory / f"{stem}.stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    norm = LogNorm(min(areas), max(areas) if max(areas)>min(areas) else min(areas)*1.01)
    cmap = plt.get_cmap("viridis")
    fig = plt.figure(figsize=(16, 9), layout="constrained")
    gs = fig.add_gridspec(2, 4, height_ratios=(1, 1.3))
    overview = fig.add_subplot(gs[0, :3])
    information = fig.add_subplot(gs[0, 3]); information.axis("off")
    left = fig.add_subplot(gs[1, :2])
    right = fig.add_subplot(gs[1, 2])
    histogram = fig.add_subplot(gs[1, 3])
    span = x1-x0
    # The first/last thirds show edge fragmentation without changing regions.
    panels = [(overview, (x0,y0,x1,y1), "All regions (no XY grid)"),
              (left, (x0,y0,x0+span/3,y1), "Left edge detail"),
              (right, (x1-span/6,y0,x1,y1), "Right edge detail")]
    domain = next(d for d in step["domains"] if d["id"] == step["profile_domain_id"])
    for panel_index, (ax, box, title) in enumerate(panels):
        ax.set_facecolor("#eef0f4")
        for i, r in enumerate(rects):
            patch = Rectangle((r.x0,r.y0),r.width,r.height,
                              facecolor=cmap(norm(r.area)),edgecolor="#17212d",linewidth=.45)
            patch.set_gid(f"region-{panel_index}-{i}")
            ax.add_patch(patch)
            if (box[0] <= (r.x0+r.x1)/2 <= box[2]
                    and r.width/(box[2]-box[0]) > .05
                    and r.height/(box[3]-box[1]) > .035):
                ax.text((r.x0+r.x1)/2,(r.y0+r.y1)/2,f"R{i}",ha="center",va="center",
                        fontsize=6,color="black" if norm(r.area)>.5 else "white",clip_on=True)
        for polygon in domain["polygons"]:
            pts=polygon["points"] + polygon["points"][:1]
            ax.plot([p[0] for p in pts],[p[1] for p in pts],color="#ec4e39",
                    linewidth=.6,linestyle="--",alpha=.8)
        ax.set_xlim(box[0],box[2]); ax.set_ylim(box[1],box[3]); ax.set_aspect("equal")
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3 if panel_index else 7))
        ax.set_title(title,fontsize=11); ax.set_xlabel("X / mm"); ax.set_ylabel("Y / mm")
    fig.colorbar(ScalarMappable(norm=norm,cmap=cmap),ax=overview,
                 orientation="horizontal",shrink=.6,pad=.08,label="Region area / mm² (log scale)")
    information.text(0,1,
        f"{len(rects)} rectangle regions\n"
        f"Physical initial step: {actual['tolerance_mm']:.4g} mm\n"
        f"Retreat limit: {actual['audit']['boundary_retreat']['max_allowed_mm']:.4g} mm\n"
        f"Retreat upper: {actual['audit']['boundary_retreat']['max_distance_upper_mm']:.4g} mm\n"
        f"Area budget: {actual['audit']['relative_area_budget']:.2%}\n"
        f"Forced lines: X={stats['forced_x_lines']}, Y={stats['forced_y_lines']}\n\n"
        f"Area min / median / max:\n{min(areas):.3g} / {statistics.median(areas):.3g} / {max(areas):.3g} mm²\n"
        f"Shortest edge min: {min(shortest):.4g} mm\n"
        f"Short edge < 1 mm: {stats['short_edge_below_1mm_count']} regions\n"
        f"Area < 1 mm²: {stats['area_below_1mm2_count']} regions\n\n"
        f"Loss: {actual['lost_area_mm2']:.4g} mm² ({actual['relative_area_error']:.2%})\n"
        f"Inscribed: {actual['audit']['inscribed']}\n"
        f"Topology preserved: {actual['audit']['topology']['preserved']}\n\n"
        "Black lines = region borders\nRed dashed = original profile\n"
        "Colors represent region area,\nnot thermal conductivity.\n"
        "All region IDs and sizes: CSV\nHover regions in SVG for details.",
        va="top",fontsize=9,linespacing=1.3)
    histogram.hist(shortest,bins=15,color="#478cab",edgecolor="white")
    histogram.set_xlabel("Shorter edge / mm"); histogram.set_ylabel("Region count")
    histogram.set_title("Region size distribution",fontsize=11)
    fig.suptitle(f"{step['name']} | Actual region partition",fontsize=15)
    fig.savefig(directory / f"{stem}.png",dpi=160)
    svg = directory / f"{stem}.svg"
    fig.savefig(svg)
    plt.close(fig)
    ns="http://www.w3.org/2000/svg"
    ET.register_namespace("",ns)
    tree=ET.parse(svg)
    for node in tree.iter():
        gid=node.attrib.get("id","")
        if gid.startswith("region-"):
            i=int(gid.rsplit("-",1)[1]); r=rects[i]
            title=ET.Element(f"{{{ns}}}title")
            title.text=f"R{i}: {r.width:.4g} × {r.height:.4g} mm; area {r.area:.4g} mm²"
            node.insert(0,title)
    tree.write(svg,encoding="utf-8",xml_declaration=True)
    return stats

