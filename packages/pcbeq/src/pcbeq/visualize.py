"""Human-viewable renderings of the equivalence stage.

Two artefacts: standard-library mask PNGs and Matplotlib conductivity figures:

* **material masks** -- one greyscale PNG per equivalented layer, containing
  exactly the black/white raster the formulas integrate over.  A reviewer can
  then *look* at what produced the numbers instead of inferring it from the JSON
  (AGENTS 验证要求 3: never judge the equivalence from a picture alone, but do
  look at it);
* **conductivity heat maps** -- one PNG per layer with ``kx``, ``ky`` and ``kz``
  side by side on a shared colour scale, plus a small gallery HTML that names
  the layers the pixels cannot.  These are rendered from the *written JSON* -- the
  file is the interface between the equivalence and everything downstream, so the
  picture is a picture of the delivered data, not of a private in-memory result.

Tone conventions in the mask PNG (one byte per pixel):

======================  ======  ==========================================
value                   tone    meaning
======================  ======  ==========================================
0                       black   copper / drill projection (material 1)
255                     white   base laminate inside the domain (material 2)
160                     grey    no computational domain: void / board-outside
======================  ======  ==========================================

Grey is not a third material.  A cell outside a layer's computational domain is
where the layer does not exist, and the equivalence never counts it as FR4
(AGENTS constraint 7); the picture keeps that distinction visible.
"""

from __future__ import annotations

import html
import math
import os
from array import array
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from .errors import EquivalenceErrorBase
from .png import (
    Rgb,
    write_greyscale_png,
    write_rgb_png,
)
from .raster import LayerMask

__all__ = [
    "VisualizeError",
    "NO_DATA_COLOUR",
    "MASK_TARGET_PX",
    "MASK_MAX_PIXELS",
    "MaskImage",
    "auto_mask_scale",
    "mask_image_size",
    "write_layer_mask",
    "MaterialMapData",
    "LayerSeries",
    "read_materialmap",
    "HeatmapOptions",
    "HeatmapImage",
    "HeatmapReport",
    "render_heatmaps",
    "GalleryEntry",
    "gallery_entries",
    "write_gallery_html",
    "format_z",
    "safe_name",
    "unique_image_stem",
]


class VisualizeError(EquivalenceErrorBase):
    """The input cannot be turned into a picture, and guessing is not an option."""


#: Missing cells remain visually distinct from low conductivity.
NO_DATA_COLOUR: Rgb = (232, 236, 241)


#: Panel titles, in drawing order.  The JSON columns are ``kx_w_mk`` ... one
#: direction per panel, all three on one shared scale so they can be compared.
PANELS: tuple[tuple[str, str], ...] = (
    ("kx", "kx_w_mk"),
    ("ky", "ky_w_mk"),
    ("kz", "kz_w_mk"),
)

_UNSAFE = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"}


def safe_name(name: str) -> str:
    """A file-name-safe rendering of a layer name.

    Layer names come from the ODB++ matrix and are usually tame, but they are
    authored text: a name with a path separator would otherwise decide where a
    PNG lands.  Only characters that cannot appear on a file system are replaced.
    """
    out = "".join("_" if (char in _UNSAFE or ord(char) < 32) else char
                  for char in name.strip())
    out = out.rstrip(". ")
    if not out:
        out = "_"
    if out.upper() in _RESERVED:
        out = "_" + out
    return out


def unique_image_stem(name: str, used: set[str]) -> str:
    """Reserve a layer image stem, including suffix and case-fold collisions.

    Different authored names can have the same filesystem-safe spelling.  The
    reserved set holds the *whole* stem so an authored ``A_B-2`` cannot replace
    the suffix assigned to a preceding ``A:B``.  Case-folding also protects
    case-insensitive filesystems such as the default Windows filesystem.
    """
    base = safe_name(name)
    stem = base
    suffix = 2
    while stem.casefold() in used:
        stem = f"{base}-{suffix}"
        suffix += 1
    used.add(stem.casefold())
    return stem


#: Longest side, in pixels, an automatically scaled mask picture aims for.
MASK_TARGET_PX = 1400
#: Ceiling on a mask picture, so a very fine raster on a narrow board cannot
#: ask for a gigapixel PNG.
MASK_MAX_PIXELS = 4_000_000


# ---------------------------------------------------------------------------
# material masks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaskImage:
    """What one written mask PNG contains, for reporting and for the CLI."""

    width: int
    height: int
    zoom_x: float
    zoom_y: float

    @property
    def size_text(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def isotropic_zoom(self) -> Optional[float]:
        """The single zoom factor, when both axes got the same one."""
        if math.isclose(self.zoom_x, self.zoom_y, rel_tol=1e-9):
            return self.zoom_x
        return None


def auto_mask_scale(mask: LayerMask, target: int = MASK_TARGET_PX) -> int:
    """Isotropic nearest-neighbour zoom that brings a raster up to ~*target*.

    A 20x20 raster is unreadable at 1:1; a 2000x2000 one is already large.  The
    zoom never interpolates, so the picture stays a sampling result.  It is only
    used when no pixel pitch is known: an isotropic zoom stretches a board whose
    X and Y raster pitches differ, see :func:`mask_image_size`.
    """
    if target < 1:
        raise ValueError(f"mask target size must be positive, got {target}")
    longest = max(mask.px, mask.py)
    return max(1, int(round(target / longest)))


def mask_image_size(mask: LayerMask,
                    pixel_step_mm: tuple[float, float]) -> tuple[int, int]:
    """Output size, in pixels, of a mask that keeps the board's proportions.

    ``pixel_step_mm`` is the physical size of one raster pixel along X and Y.
    The scale is the coarsest one that both reaches the target size and never
    puts two raster pixels on one output pixel (that would sample features
    away); if that is still larger than :data:`MASK_MAX_PIXELS`, it is reduced,
    and the caller is expected to say so rather than pretend.
    """
    step_x, step_y = (float(value) for value in pixel_step_mm)
    if not (step_x > 0.0 and step_y > 0.0):
        raise ValueError(
            f"pixel step must be positive, got {pixel_step_mm!r}")
    width_mm = mask.px * step_x
    height_mm = mask.py * step_y
    scale = max(MASK_TARGET_PX / max(width_mm, height_mm),
                1.0 / min(step_x, step_y))
    width = max(1, int(round(width_mm * scale)))
    height = max(1, int(round(height_mm * scale)))
    if width * height > MASK_MAX_PIXELS:
        scale *= math.sqrt(MASK_MAX_PIXELS / (width * height))
        width = max(1, int(round(width_mm * scale)))
        height = max(1, int(round(height_mm * scale)))
    return width, height


def write_layer_mask(mask: LayerMask, path: str,
                     scale: Optional[int] = None,
                     pixel_step_mm: Optional[tuple[float, float]] = None
                     ) -> MaskImage:
    """Write one layer mask as a greyscale PNG; return the size it was given.

    An explicit *scale* is an isotropic nearest-neighbour zoom of the raster.
    Giving *pixel_step_mm* instead keeps the board's physical proportions,
    which is what makes a mask comparable with its heat map (and with the board
    it came from).
    """
    if scale is not None:
        zoom = int(scale)
        if zoom < 1:
            raise ValueError(f"mask scale must be positive, got {zoom}")
        write_greyscale_png(path, mask.px * zoom, mask.py * zoom,
                            mask.top_down_rows(zoom))
        return MaskImage(mask.px * zoom, mask.py * zoom, float(zoom), float(zoom))
    if pixel_step_mm is not None:
        width, height = mask_image_size(mask, pixel_step_mm)
        write_greyscale_png(path, width, height,
                            mask.resampled_rows(width, height))
        return MaskImage(width, height, width / mask.px, height / mask.py)
    zoom = auto_mask_scale(mask)
    write_greyscale_png(path, mask.px * zoom, mask.py * zoom,
                        mask.top_down_rows(zoom))
    return MaskImage(mask.px * zoom, mask.py * zoom, float(zoom), float(zoom))


# ---------------------------------------------------------------------------
# reading the delivered JSON
# ---------------------------------------------------------------------------

@dataclass
class LayerSeries:
    """One layer's cells, in JSON order, straight off the file."""

    name: str
    layer_type: str = ""
    z_start_mm: Optional[float] = None
    z_end_mm: Optional[float] = None
    thickness_known: bool = False
    i: array = field(default_factory=lambda: array("i"))
    j: array = field(default_factory=lambda: array("i"))
    kx: array = field(default_factory=lambda: array("d"))
    ky: array = field(default_factory=lambda: array("d"))
    kz: array = field(default_factory=lambda: array("d"))
    rectangles: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.i)

    def k_bounds(self) -> tuple[float, float]:
        """Smallest and largest conductivity over all three directions."""
        low = math.inf
        high = -math.inf
        for values in (self.kx, self.ky, self.kz):
            if values:
                low = min(low, min(values))
                high = max(high, max(values))
        if low > high:
            raise VisualizeError(
                f"layer {self.name!r} has no readable conductivity values")
        return low, high

    def values(self, column: str) -> array:
        return {"kx_w_mk": self.kx, "ky_w_mk": self.ky,
                "kz_w_mk": self.kz}[column]


@dataclass
class MaterialMapData:
    """The whole JSON as a rectilinear grid plus one series per layer."""

    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    series: list[LayerSeries]
    row_count: int = 0
    skipped_invalid: int = 0
    #: Cell-grid column index -> interval index into :attr:`x_edges`.
    x_interval_of_column: dict[int, int] = field(default_factory=dict, repr=False)
    y_interval_of_row: dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def column_count(self) -> int:
        return max(0, len(self.x_edges) - 1)

    @property
    def row_band_count(self) -> int:
        return max(0, len(self.y_edges) - 1)

    def bounds_mm(self) -> tuple[float, float, float, float]:
        return (self.x_edges[0], self.y_edges[0],
                self.x_edges[-1], self.y_edges[-1])


def read_materialmap(path: str) -> MaterialMapData:
    """Strict JSON input; reconstruct cells in Y-major/X-fast row order.

    A missing/invalid property row is fatal: skipping would shift every remaining
    value onto a different cell. No ODB++ or run report is needed for heatmaps.
    """
    from .materialmap import read_document, iter_cells
    try:
        document = read_document(path)
    except (OSError, ValueError) as exc:
        raise VisualizeError(f"cannot read MaterialMap JSON {path}: {exc}") from exc
    xs = tuple(sorted({x for r in document.regions for x in r.x_lines_mm}))
    ys = tuple(sorted({y for r in document.regions for y in r.y_lines_mm}))
    xi, yi = {x:i for i,x in enumerate(xs)}, {y:j for j,y in enumerate(ys)}
    layers = {}
    count = 0
    for region, bounds, values in iter_cells(document):
        name = region.layer.name
        if name not in layers:
            a,b = region.bounds_mm.z
            layers[name] = LayerSeries(name=name, layer_type=region.layer.type,
                z_start_mm=a, z_end_mm=b, thickness_known=a is not None and b is not None)
        series = layers[name]
        series.rectangles.append(bounds)
        series.i.append(xi[bounds[0]])
        series.j.append(yi[bounds[1]])
        series.kx.append(values[0]); series.ky.append(values[1]); series.kz.append(values[2])
        count += 1
    return MaterialMapData(x_edges=xs, y_edges=ys, series=list(layers.values()),
        row_count=count, skipped_invalid=0,
        x_interval_of_column={i:i for i in range(len(xs)-1)},
        y_interval_of_row={i:i for i in range(len(ys)-1)})


# ---------------------------------------------------------------------------
# conductivity figures: one layer, three axes, one shared colour bar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeatmapOptions:
    """Output resolution and presentation; these never change JSON geometry."""

    panel_width_px: int = 1440
    max_panel_px: int = 2400
    shared_scale: bool = False
    draw_grid: bool = False
    font_size_pt: float = 12.0

    def __post_init__(self) -> None:
        if self.panel_width_px < 32:
            raise ValueError("panel_width_px must be at least 32")
        if self.max_panel_px < self.panel_width_px:
            raise ValueError("max_panel_px is smaller than panel_width_px")
        if not math.isfinite(self.font_size_pt) or self.font_size_pt <= 0:
            raise ValueError("font_size_pt must be finite and positive")


@dataclass
class HeatmapImage:
    layer: str
    path: str
    index: int
    count: int
    vmin: float
    vmax: float
    cells: int
    # Measured artist bounds, in top-down output pixels, for visual QA.
    panel_boxes: tuple[tuple[int, int, int, int], ...] = ()
    colourbar_box: tuple[int, int, int, int] = (0, 0, 0, 0)


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


# ---------------------------------------------------------------------------
# gallery
# ---------------------------------------------------------------------------

@dataclass
class GalleryEntry:
    """One layer as shown in the gallery: what the picture cannot say."""

    name: str
    index: int
    count: int
    layer_type: str = ""
    z_text: str = ""
    k_text: str = ""
    cells: int = 0
    heatmap: Optional[str] = None
    mask: Optional[str] = None


_GALLERY_STYLE = (
    "body{max-width:1600px;margin:32px auto;padding:0 24px;"
    "font:15px/1.6 system-ui,sans-serif;color:#17253b;background:#f7f9fc}"
    "h1{font-size:24px;margin:0 0 8px}h2{font-size:17px;margin:26px 0 4px}"
    ".meta{color:#465569;font-size:13px;margin:0 0 8px}"
    ".layer{border-top:1px solid #dde4ef;padding-top:10px}"
    "img{max-width:100%;border:1px solid #dde4ef;background:#fff;"
    "display:block;margin:8px 0;image-rendering:pixelated}"
    "nav a{display:inline-block;margin:2px 10px 2px 0;color:#135baf;"
    "text-decoration:none}nav a:hover{text-decoration:underline}"
    "code{background:#eef2f8;padding:1px 4px;border-radius:3px}"
    "p.note{color:#465569}"
    ".heatmap-view{overflow-x:auto} .heatmap-view img{width:100%;min-width:1000px;max-width:none;box-sizing:border-box;image-rendering:auto}"
    ".mask img{max-height:360px;max-width:100%;width:auto}"
    "summary{cursor:pointer;color:#135baf;padding:10px 0}"
)


def write_gallery_html(path: str, title: str, entries: Sequence[GalleryEntry],
                       *, note: str = "") -> str:
    """Write the index page that names what the PNGs deliberately do not."""
    lines = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_GALLERY_STYLE}</style>",
        f"<h1>{html.escape(title)}</h1>",
        "<p class='note'>逐层展示材料掩膜（光栅化的黑/白结果，灰=该层在此无计算域）"
        "与该层的 kx/ky/kz 等效热导率热图。数值与单位以 MaterialMap.json 与其 meta 为准，"
        "本页只便于人眼核对。</p>",
        "<p class='note'>三个方向始终在同一张热图中；窄屏可横向滚动，点击图像可查看原始 PNG。"
        "三个方向使用同一色标，灰色表示该层在此无单元。</p>",
    ]
    if note:
        lines.append(f"<p class='note'>{html.escape(note)}</p>")
    links = []
    for entry in entries:
        label = f"{entry.index}. {entry.name}"
        links.append(f"<a href='#layer-{entry.index}'>{html.escape(label)}</a>")
    lines.append("<nav>" + " ".join(links) + "</nav>")

    for entry in entries:
        lines.append(f"<section class='layer' id='layer-{entry.index}'>")
        lines.append(f"<h2>{entry.index}/{entry.count} &middot; "
                     f"{html.escape(entry.name)}"
                     f"{' &middot; ' + html.escape(entry.layer_type) if entry.layer_type else ''}"
                     f"</h2>")
        meta = [html.escape(entry.z_text) if entry.z_text else "z ?"]
        if entry.k_text:
            meta.append(html.escape(entry.k_text))
        meta.append(f"{entry.cells} cell(s)")
        lines.append("<p class='meta'>" + " &middot; ".join(meta) + "</p>")
        if entry.mask:
            lines.append("<details class='mask'><summary>材料掩膜（点击展开）</summary>")
            lines.append(f"<a href='{html.escape(entry.mask, quote=True)}'><img src='{html.escape(entry.mask, quote=True)}' "
                         f"alt='{html.escape(entry.name, quote=True)} material mask' loading='lazy'></a></details>")
        if entry.heatmap:
            href = html.escape(entry.heatmap, quote=True)
            alt = html.escape(f"{entry.name} kx ky kz heat maps", quote=True)
            lines.append(f"<div class='heatmap-view'><a href='{href}'>"
                         f"<img src='{href}' alt='{alt}' loading='lazy'></a></div>")
        lines.append("</section>")
    lines.append("</body></html>")
    document = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)
    return path


def z_text(series: LayerSeries) -> str:
    """The gallery's Z sentence for one layer, absolute and honest about trust."""
    return format_z(series.z_start_mm, series.z_end_mm, series.thickness_known)


def gallery_entries(report: HeatmapReport, *,
                    masks: Optional[Mapping[str, str]] = None,
                    gallery_dir: Optional[str] = None) -> list[GalleryEntry]:
    """Join one heat-map run with the mask files written for the same layers.

    Image links are made relative to *gallery_dir* -- the directory the gallery
    HTML is about to be written into -- because the page and the PNGs are not
    always in the same place.
    """
    by_name = {series.name: series for series in report.data.series}
    entries: list[GalleryEntry] = []
    for image in report.images:
        series = by_name.get(image.layer)
        if gallery_dir:
            href = os.path.relpath(image.path, gallery_dir).replace(os.sep, "/")
        else:
            href = os.path.basename(image.path)
        entries.append(GalleryEntry(
            name=image.layer,
            index=image.index,
            count=image.count,
            layer_type="" if series is None else series.layer_type,
            z_text="z ?" if series is None else z_text(series),
            k_text=f"k 范围 [{_format(image.vmin)}, {_format(image.vmax)}] "
                   f"W/(m·K)（kx/ky/kz 同一色标）",
            cells=image.cells,
            heatmap=href,
            mask=None if not masks else masks.get(image.layer),
        ))
    return entries
