"""Render MaterialMap conductivity without input geometry or Python product imports."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional, Sequence
from .png import write_rgb_png
from .heatmap_data import (VisualizeError, NO_DATA_COLOUR, PANELS, LayerSeries,
    MaterialMapData, HeatmapOptions, HeatmapImage, read_materialmap, unique_image_stem)


def _format(value: float) -> str:
    return f"{value:.4g}"


def format_z(z_start_mm: Optional[float], z_end_mm: Optional[float],
             thickness_known: bool) -> str:
    """Absolute Z with an explicit indication of unknown thickness."""
    if z_start_mm is None or z_end_mm is None:
        return "z ?"
    text = f"z {_format(z_start_mm)}..{_format(z_end_mm)} mm"
    return text if thickness_known else text + " ?"


def render_layer_heatmap(series: LayerSeries, data: MaterialMapData,
                         bounds: tuple[float, float], path: str, *,
                         index: int, count: int,
                         options: HeatmapOptions) -> HeatmapImage:
    """Render physical JSON rectangles with Agg, without resampling the values.

    Layout is specified in inches, independently of PNG resolution. Axes keep
    equal physical scaling; every direction uses the same normalisation. Only
    cells actually present in the JSON are painted or outlined.
    """
    import textwrap
    import numpy as np
    from matplotlib import colormaps, font_manager
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import ListedColormap, Normalize
    from matplotlib.figure import Figure
    from matplotlib.cm import ScalarMappable
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator, FuncFormatter

    x0, y0, x1, y1 = data.bounds_mm()
    dpi = options.panel_width_px / 4.8
    board_height = min(4.8 * (y1 - y0) / (x1 - x0), options.max_panel_px / dpi)
    body_height = max(0.65, board_height)
    slot, gap, left, right = 4.8, 0.65, 0.85, 0.35
    width = left + 3 * slot + 2 * gap + right
    title_lines = textwrap.wrap(f"Layer {index}/{count}  |  {series.name}", width=85)
    header = 1.35 + 0.3 * (len(title_lines) - 1)
    bottom = 1.65
    height = header + body_height + bottom
    figure = Figure(figsize=(width, height), dpi=dpi, facecolor="white")
    renderer = FigureCanvasAgg(figure)
    font = options.font_size_pt
    family = "DejaVu Sans"
    available = {item.name for item in font_manager.fontManager.ttflist}
    for candidate in ("Microsoft YaHei", "Noto Sans CJK SC", "SimHei"):
        if candidate in available:
            family = candidate
            break
    text_style = dict(fontfamily=family, color="#263445")
    figure.text(left / width, 1 - 0.14 / height, "\n".join(title_lines),
                va="top", fontsize=font + 4, weight="bold", **text_style)
    z = format_z(series.z_start_mm, series.z_end_mm, series.thickness_known)
    z += f"   |   {series.layer_type}   |   {len(series):,} cells"
    if not series.thickness_known:
        z += "   |   thickness unknown"
    figure.text(left / width, 1 - (0.6 + 0.3 * (len(title_lines) - 1)) / height, z,
                va="top", fontsize=font - 1, **text_style)

    cmap = colormaps["viridis"]
    uniform = bounds[0] == bounds[1]
    if uniform:
        span = max(abs(bounds[0]) * 0.01, 1e-6)
        norm = Normalize(bounds[0] - span, bounds[0] + span)
        cmap = ListedColormap([cmap(0.5)])
    else:
        norm = Normalize(*bounds)
    cells = np.asarray(series.rectangles, dtype=float)
    vertices = np.stack((cells[:, [0, 1]], cells[:, [2, 1]],
                         cells[:, [2, 3]], cells[:, [0, 3]]), axis=1)
    absent = tuple(value / 255 for value in NO_DATA_COLOUR)
    axes = []
    for panel, (name, field) in enumerate(PANELS):
        axis = figure.add_axes([(left + panel * (slot + gap)) / width,
                                bottom / height, slot / width, body_height / height])
        axis.set_facecolor(absent)
        collection = PolyCollection(vertices, array=np.asarray(series.values(field)),
                                    cmap=cmap, norm=norm,
                                    edgecolors="#697787" if options.draw_grid else "none",
                                    linewidths=0.35 if options.draw_grid else 0,
                                    antialiaseds=False)
        axis.add_collection(collection)
        axis.set_xlim(x0, x1)
        axis.set_ylim(y0, y1)
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(name, fontsize=font + 2, fontfamily=family,
                       color="#263445", weight="bold", pad=10)
        axis.set_xlabel("X (mm)", fontsize=font - 1, fontfamily=family, labelpad=6)
        if panel == 0:
            axis.set_ylabel("Y (mm)", fontsize=font - 1, fontfamily=family, labelpad=6)
        axis.xaxis.set_major_locator(MaxNLocator(nbins=4))
        axis.yaxis.set_major_locator(MaxNLocator(nbins=4))
        axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        axis.tick_params(labelsize=font - 2, colors="#526071", length=3,
                         labelleft=panel == 0)
        for spine in axis.spines.values():
            spine.set_color("#8995a3")
            spine.set_linewidth(0.65)
        axes.append(axis)

    bar_axis = figure.add_axes([0.25, 0.62 / height, 0.5, 0.18 / height])
    colourbar = figure.colorbar(ScalarMappable(norm=norm, cmap=cmap),
                               cax=bar_axis, orientation="horizontal")
    colourbar.set_label("Thermal conductivity  [W/(m·K)]" +
                        ("  —  uniform" if uniform else ""),
                        fontsize=font - 1, fontfamily=family, labelpad=6)
    ticks = [bounds[0]] if uniform else np.linspace(*bounds, 5)
    colourbar.set_ticks(ticks)
    colourbar.set_ticklabels([_format(float(value)) for value in ticks])
    colourbar.ax.tick_params(labelsize=font - 2, length=3, colors="#526071")
    colourbar.outline.set_visible(False)
    figure.legend(handles=[Patch(facecolor=absent, edgecolor="none",
                                 label="No cell / outside layer domain")],
                  loc="lower left", bbox_to_anchor=(left / width, 0.005),
                  frameon=False, prop={"family": family, "size": font - 2})
    renderer.draw()
    rgba = np.asarray(renderer.buffer_rgba())
    pixel_h, pixel_w = rgba.shape[:2]
    def box(axis):
        a, b, w, h = axis.get_window_extent().bounds
        return (round(a), round(pixel_h - b - h), round(a + w), round(pixel_h - b))
    panel_boxes = tuple(box(axis) for axis in axes)
    bar_box = box(bar_axis)
    write_rgb_png(path, pixel_w, pixel_h,
                  (row[:, :3].tobytes() for row in rgba))
    figure.clear()
    return HeatmapImage(series.name, path, index, count, *bounds, len(series),
                        panel_boxes=panel_boxes, colourbar_box=bar_box)


@dataclass
class HeatmapReport:
    """Everything one heat-map run produced, including the data it read."""

    map_path: str
    out_dir: str
    data: MaterialMapData
    images: list[HeatmapImage]


def render_heatmaps(map_path: str, out_dir: str, *,
                    options: Optional[HeatmapOptions] = None,
                    layers: Optional[Sequence[str]] = None) -> HeatmapReport:
    """Render one kx/ky/kz heat map per layer of *map_path* into *out_dir*."""
    opts = options or HeatmapOptions()
    data = read_materialmap(map_path)
    wanted = None if layers is None else list(dict.fromkeys(layers))
    if wanted is not None:
        known = {series.name for series in data.series}
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise VisualizeError(
                f"{map_path}: no such layer(s): {', '.join(unknown)}")
        selected = [series for series in data.series if series.name in set(wanted)]
    else:
        selected = list(data.series)
    if not selected:
        raise VisualizeError(f"{map_path}: no layer to draw")

    bounds = [series.k_bounds() for series in selected]
    if opts.shared_scale:
        low = min(item[0] for item in bounds)
        high = max(item[1] for item in bounds)
        bounds = [(low, high)] * len(selected)

    os.makedirs(out_dir, exist_ok=True)
    used: set[str] = set()
    images: list[HeatmapImage] = []
    for position, (series, series_bounds) in enumerate(zip(selected, bounds), start=1):
        stem = unique_image_stem(series.name, used)
        path = os.path.join(out_dir, f"{stem}.png")
        images.append(render_layer_heatmap(
            series, data, series_bounds, path,
            index=position, count=len(selected), options=opts))
    return HeatmapReport(map_path=map_path, out_dir=out_dir, data=data,
                         images=images)
