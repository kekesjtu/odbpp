"""Human-viewable renderings of the equivalence stage.

Two artefacts, both deliberately dependency-free (see :mod:`odbpp.png`):

* **material masks** -- one greyscale PNG per equivalented layer, containing
  exactly the black/white raster the formulas integrate over.  A reviewer can
  then *look* at what produced the numbers instead of inferring it from the CSV
  (AGENTS 验证要求 3: never judge the equivalence from a picture alone, but do
  look at it);
* **conductivity heat maps** -- one PNG per layer with ``kx``, ``ky`` and ``kz``
  side by side on a shared colour scale, plus a small gallery HTML that names
  the layers the pixels cannot.  These are rendered from the *written CSV* -- the
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

import csv
import html
import math
import os
from array import array
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from .errors import OdbError
from .png import (
    Canvas,
    FONT_HEIGHT,
    Rgb,
    draw_text,
    text_width,
    write_greyscale_png,
)
from .raster import LayerMask

__all__ = [
    "VisualizeError",
    "NO_DATA",
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


class VisualizeError(OdbError):
    """The input cannot be turned into a picture, and guessing is not an option."""


#: Palette index reserved for "this layer has no cell here".
NO_DATA = 255
#: Number of palette indices available for values (0 .. 254).
_DATA_LEVELS = NO_DATA

#: Colour of an absent cell.  Light and neutral on purpose: the value ramp starts
#: dark, so absence never reads as a low value.
NO_DATA_COLOUR: Rgb = (222, 222, 226)

#: Our own blue -> cyan -> green -> yellow -> red ramp.  Anchor colours, linearly
#: interpolated; deliberately not a copy of a published colour map.
_COLOUR_STOPS: tuple[tuple[float, Rgb], ...] = (
    (0.00, (12, 12, 64)),
    (0.18, (24, 76, 176)),
    (0.36, (0, 160, 200)),
    (0.54, (0, 172, 96)),
    (0.72, (216, 208, 24)),
    (0.86, (244, 132, 4)),
    (1.00, (176, 16, 32)),
)

#: Panel titles, in drawing order.  The CSV columns are ``kx_w_mk`` ... one
#: direction per panel, all three on one shared scale so they can be compared.
PANELS: tuple[tuple[str, str], ...] = (
    ("kx", "kx_w_mk"),
    ("ky", "ky_w_mk"),
    ("kz", "kz_w_mk"),
)

#: Columns the heat map needs.  A CSV without them is a different layout, not a
#: degraded one, so it is refused rather than partially drawn.
_CSV_REQUIRED = (
    "layer", "layer_type", "cell_i", "cell_j",
    "cell_x0_mm", "cell_y0_mm", "cell_x1_mm", "cell_y1_mm",
    "z_start_mm", "z_end_mm", "z_thickness_known",
    "kx_w_mk", "ky_w_mk", "kz_w_mk",
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
MASK_TARGET_PX = 700
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
# reading the delivered CSV
# ---------------------------------------------------------------------------

@dataclass
class LayerSeries:
    """One layer's cells, in CSV order, straight off the file."""

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
    """The whole CSV as a rectilinear grid plus one series per layer."""

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


def _key(value: float) -> float:
    """Round a coordinate to a stable dictionary key (1e-6 mm = 1 nm)."""
    return round(float(value), 6)


def _record(store: dict[int, float], index: int, value: float, column: str,
            path: str, line_no: int) -> None:
    existing = store.get(index)
    if existing is None:
        store[index] = value
    elif abs(existing - value) > 1e-6 * max(1.0, abs(existing)):
        raise VisualizeError(
            f"{path}:{line_no}: {column} for index {index} is {value!r}, but "
            f"{existing!r} was already recorded; the CSV is not one coherent "
            f"rectilinear grid, so it cannot be drawn as a heat map")


def _float_field(row: Mapping[str, str], column: str, path: str,
                 line_no: int) -> Optional[float]:
    raw = (row.get(column) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise VisualizeError(
            f"{path}:{line_no}: {column}={raw!r} is not a number") from None


def read_materialmap(path: str) -> MaterialMapData:
    """Read a ``MaterialMap.csv`` into per-layer series plus the grid they span.

    Rows whose conductivity is missing, non-finite or non-positive are counted
    and skipped, never drawn as a value: a heat map that silently paints a bad
    number is worse than one with a hole in it.
    """
    x0: dict[int, float] = {}
    x1: dict[int, float] = {}
    y0: dict[int, float] = {}
    y1: dict[int, float] = {}
    by_name: dict[str, LayerSeries] = {}
    order: list[str] = []
    rows = 0
    invalid = 0

    try:
        handle = open(path, encoding="utf-8", newline="")
    except OSError as exc:
        raise VisualizeError(f"cannot read {path}: {exc}") from None
    with handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = [column for column in _CSV_REQUIRED if column not in fields]
        if missing:
            raise VisualizeError(
                f"{path}: not an odbpp MaterialMap CSV; missing column(s) "
                f"{', '.join(missing)}")
        for line_no, row in enumerate(reader, start=2):
            rows += 1
            name = (row.get("layer") or "").strip()
            if not name:
                invalid += 1
                continue
            try:
                i = int(row.get("cell_i") or "")
                j = int(row.get("cell_j") or "")
            except (TypeError, ValueError):
                raise VisualizeError(
                    f"{path}:{line_no}: cell_i/cell_j are not integers") from None
            geometry = [_float_field(row, column, path, line_no) for column in
                        ("cell_x0_mm", "cell_y0_mm", "cell_x1_mm", "cell_y1_mm")]
            values = [_float_field(row, column, path, line_no) for _, column
                      in PANELS]
            if any(value is None for value in geometry + values):
                invalid += 1
                continue
            xa, ya, xb, yb = geometry  # type: ignore[misc]
            if (xb <= xa or yb <= ya
                    or not all(math.isfinite(value) and value > 0.0
                               for value in values)):  # type: ignore[union-attr]
                invalid += 1
                continue

            x0[rows] = xa
            x1[rows] = xb
            y0[rows] = ya
            y1[rows] = yb

            series = by_name.get(name)
            if series is None:
                z_start = _float_field(row, "z_start_mm", path, line_no)
                z_end = _float_field(row, "z_end_mm", path, line_no)
                known = (row.get("z_thickness_known") or "").strip()
                series = LayerSeries(
                    name=name,
                    layer_type=(row.get("layer_type") or "").strip(),
                    z_start_mm=z_start if z_start is not None and math.isfinite(z_start) else None,
                    z_end_mm=z_end if z_end is not None and math.isfinite(z_end) else None,
                    thickness_known=known in ("1", "true", "True"),
                )
                by_name[name] = series
                order.append(name)
            series.rectangles.append((xa,ya,xb,yb))
            series.i.append(rows)
            series.j.append(rows)
            series.kx.append(values[0])  # type: ignore[arg-type]
            series.ky.append(values[1])  # type: ignore[arg-type]
            series.kz.append(values[2])  # type: ignore[arg-type]

    if not by_name:
        raise VisualizeError(
            f"{path}: no readable cell rows ({rows} line(s) seen, "
            f"{invalid} unusable)")

    x_edges = tuple(sorted({_key(value) for value in
                            list(x0.values()) + list(x1.values())}))
    y_edges = tuple(sorted({_key(value) for value in
                            list(y0.values()) + list(y1.values())}))
    if len(x_edges) < 2 or len(y_edges) < 2:
        raise VisualizeError(
            f"{path}: the cells do not span a two-dimensional area")

    x_index = {value: index for index, value in enumerate(x_edges)}
    y_index = {value: index for index, value in enumerate(y_edges)}
    x_interval = {column: x_index[_key(value)] for column, value in x0.items()}
    y_interval = {row_index: y_index[_key(value)] for row_index, value in y0.items()}

    return MaterialMapData(
        x_edges=x_edges, y_edges=y_edges,
        series=[by_name[name] for name in order],
        row_count=rows, skipped_invalid=invalid,
        x_interval_of_column=x_interval, y_interval_of_row=y_interval,
    )


# ---------------------------------------------------------------------------
# colour
# ---------------------------------------------------------------------------

def _interpolate(t: float) -> Rgb:
    stops = _COLOUR_STOPS
    if t <= stops[0][0]:
        return stops[0][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            span = t1 - t0
            weight = 0.0 if span <= 0.0 else (t - t0) / span
            return (int(round(c0[0] + (c1[0] - c0[0]) * weight)),
                    int(round(c0[1] + (c1[1] - c0[1]) * weight)),
                    int(round(c0[2] + (c1[2] - c0[2]) * weight)))
    return stops[-1][1]


def _palette() -> bytes:
    """256 RGB triples: 0..254 the value ramp, 255 :data:`NO_DATA_COLOUR`."""
    table = bytearray(_DATA_LEVELS * 3)
    for index in range(_DATA_LEVELS):
        t = index / (_DATA_LEVELS - 1)
        r, g, b = _interpolate(t)
        table[3 * index:3 * index + 3] = bytes((r, g, b))
    table += bytes(NO_DATA_COLOUR)
    return bytes(table)


def _channel_tables(palette: bytes) -> tuple[bytes, bytes, bytes]:
    """Per-channel 256-entry translation tables for a fast palette lookup."""
    return (palette[0::3], palette[1::3], palette[2::3])


def _quantise(value: float, vmin: float, vmax: float) -> int:
    if not vmax > vmin:
        return _DATA_LEVELS // 2
    t = (value - vmin) / (vmax - vmin)
    if t <= 0.0:
        return 0
    if t >= 1.0:
        return _DATA_LEVELS - 1
    return int(t * (_DATA_LEVELS - 1) + 0.5)


def _rgb_row(indices: bytes, column_source: array,
             channels: tuple[bytes, bytes, bytes]) -> bytes:
    """Expand one cell-index row to panel width and colour it."""
    expanded = bytes(map(indices.__getitem__, column_source))
    out = bytearray(3 * len(expanded))
    out[0::3] = expanded.translate(channels[0])
    out[1::3] = expanded.translate(channels[1])
    out[2::3] = expanded.translate(channels[2])
    return bytes(out)


# ---------------------------------------------------------------------------
# heat map rendering
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeatmapOptions:
    """Layout knobs.  Defaults keep one layer's sheet readable on its own."""

    #: Width of one panel in pixels; height follows the board aspect ratio.
    panel_width_px: int = 720
    #: Upper bound for either panel side, so an extremely long board cannot
    #: allocate a gigapixel canvas.
    max_panel_px: int = 2400
    #: Use one colour scale for every layer instead of one per layer.  Per-layer
    #: is the default because it resolves the layer; shared makes layers
    #: comparable to each other at the cost of contrast.
    shared_scale: bool = False
    #: Outline only cells present in the CSV; missing layer domains stay blank.
    draw_grid: bool = False
    grid_colour: Rgb = (110, 110, 110)
    font_scale: int = 2
    margin_px: int = 14
    gap_px: int = 18
    text_colour: Rgb = (28, 32, 40)
    frame_colour: Rgb = (70, 76, 88)
    background: Rgb = (255, 255, 255)

    def __post_init__(self) -> None:
        if self.panel_width_px < 32:
            raise ValueError(
                f"panel_width_px must be at least 32, got {self.panel_width_px}")
        if self.max_panel_px < self.panel_width_px:
            raise ValueError(
                f"max_panel_px={self.max_panel_px} is smaller than "
                f"panel_width_px={self.panel_width_px}")
        if self.font_scale < 1:
            raise ValueError(f"font_scale must be positive, got {self.font_scale}")


@dataclass
class HeatmapImage:
    """What one written heat map contains, for reporting and for the gallery."""

    layer: str
    path: str
    index: int
    count: int
    vmin: float
    vmax: float
    cells: int


def _format(value: float) -> str:
    """Compact label: four significant digits, no gratuitous exponent."""
    text = f"{value:.4g}"
    return text


def _pixel_edges(edges: Sequence[float], scale: float) -> list[int]:
    """Pixel boundaries for *edges*, rounded cumulatively.

    Rounding every edge against the same origin keeps the frame the board's
    size in both axes.  A cell narrower than one pixel can therefore end up with
    no pixel of its own when the grid is finer than the panel: the picture is a
    view, the CSV is the data, and stretching the panel to keep every cell
    visible would misreport the board's proportions (ask for a wider
    ``panel_width_px`` when every cell must be seen).
    """
    return [int(round((value - edges[0]) * scale)) for value in edges]


def _z_label(series: LayerSeries) -> str:
    return format_z(series.z_start_mm, series.z_end_mm, series.thickness_known)


def format_z(z_start_mm: Optional[float], z_end_mm: Optional[float],
             thickness_known: bool) -> str:
    """The absolute-Z sentence, labelled when the thickness is not trusted."""
    if z_start_mm is None or z_end_mm is None:
        return "z ?"
    text = f"z {_format(z_start_mm)}..{_format(z_end_mm)} mm"
    if not thickness_known:
        text += " ?"
    return text


def _column_source(pixels: Sequence[int]) -> array:
    """For each output column, the cell-grid interval index it shows."""
    source = array("i")
    for index in range(len(pixels) - 1):
        source.extend([index] * (pixels[index + 1] - pixels[index]))
    return source


def render_layer_heatmap(series: LayerSeries, cols: array, rows: array,
                         data: MaterialMapData, bounds: tuple[float, float],
                         path: str, *, index: int, count: int,
                         options: HeatmapOptions) -> HeatmapImage:
    """Draw ``kx``/``ky``/``kz`` for one layer as a single PNG."""
    columns = data.column_count
    bands = data.row_band_count
    width_mm = data.x_edges[-1] - data.x_edges[0]
    height_mm = data.y_edges[-1] - data.y_edges[0]
    scale = options.panel_width_px / width_mm
    scale = min(scale, options.max_panel_px / max(width_mm, height_mm))
    xs = _pixel_edges(data.x_edges, scale)
    ys = _pixel_edges(data.y_edges, scale)
    panel_w = xs[-1]
    panel_h = ys[-1]

    palette = _palette()
    channels = _channel_tables(palette)
    column_source = _column_source(xs)

    font = options.font_scale
    line_h = FONT_HEIGHT * font + 6
    bar_h = max(10, 4 * font)
    header_h = 2 * line_h
    title_h = line_h
    bar_block = 10 + bar_h + 4 + line_h
    margin = options.margin_px
    gap = options.gap_px

    content_w = 3 * panel_w + 2 * gap
    width = margin * 2 + content_w
    height = margin * 2 + header_h + title_h + panel_h + bar_block
    canvas = Canvas(width, height, options.background)

    # Header: which layer of how many, and the absolute Z the cells belong to.
    cursor = margin
    draw_text(canvas, cursor, margin, f"L{index}/{count}", options.text_colour, font)
    cursor += text_width(f"L{index}/{count}", font) + 16
    swatch = FONT_HEIGHT * font - 2
    canvas.rect(cursor, margin, cursor + swatch, margin + swatch, NO_DATA_COLOUR)
    canvas.frame(cursor, margin, cursor + swatch, margin + swatch, options.frame_colour)
    draw_text(canvas, cursor + swatch + 6, margin, "n/a", options.text_colour, font)
    draw_text(canvas, margin, margin + line_h, _z_label(series),
              options.text_colour, font)

    panel_y = margin + header_h + title_h
    bar_y = panel_y + panel_h + 10
    for panel, (title, field) in enumerate(PANELS):
        panel_x = margin + panel * (panel_w + gap)
        draw_text(canvas, panel_x, margin + header_h, f"{title}  W/(m.K)",
                  options.text_colour, font)
        canvas.rect(panel_x,panel_y,panel_x+panel_w,panel_y+panel_h,NO_DATA_COLOUR)
        values = series.values(field)
        for cell, (xa,ya,xb,yb) in enumerate(series.rectangles):
            left = round((xa-data.x_edges[0])*scale)
            right = round((xb-data.x_edges[0])*scale)
            top = panel_h-round((yb-data.y_edges[0])*scale)
            bottom = panel_h-round((ya-data.y_edges[0])*scale)
            level = _quantise(values[cell], *bounds)
            colour = tuple(palette[3*level:3*level+3])
            canvas.rect(panel_x+left,panel_y+top,panel_x+right,panel_y+bottom,colour)
            if options.draw_grid and right>left and bottom>top:
                canvas.frame(panel_x+left,panel_y+top,panel_x+right,panel_y+bottom,
                             options.grid_colour)
        canvas.frame(panel_x, panel_y, panel_x + panel_w, panel_y + panel_h,
                     options.frame_colour)

        # Colour bar, labelled with the numeric ends of this scale.
        gradient = bytearray(3 * panel_w)
        for column in range(panel_w):
            t = 0.0 if panel_w < 2 else column / (panel_w - 1)
            level = int(t * (_DATA_LEVELS - 1) + 0.5)
            gradient[3 * column:3 * column + 3] = palette[3 * level:3 * level + 3]
        for y in range(bar_y, bar_y + bar_h):
            canvas.blit_row(y, panel_x, bytes(gradient))
        canvas.frame(panel_x, bar_y, panel_x + panel_w, bar_y + bar_h,
                     options.frame_colour)
        low = _format(bounds[0])
        high = _format(bounds[1])
        draw_text(canvas, panel_x, bar_y + bar_h + 4, low, options.text_colour, font)
        draw_text(canvas, panel_x + panel_w - text_width(high, font),
                  bar_y + bar_h + 4, high, options.text_colour, font)

    canvas.save(path)
    return HeatmapImage(layer=series.name, path=path, index=index, count=count,
                        vmin=bounds[0], vmax=bounds[1], cells=len(series))


@dataclass
class HeatmapReport:
    """Everything one heat-map run produced, including the data it read."""

    csv_path: str
    out_dir: str
    data: MaterialMapData
    images: list[HeatmapImage]


def render_heatmaps(csv_path: str, out_dir: str, *,
                    options: Optional[HeatmapOptions] = None,
                    layers: Optional[Sequence[str]] = None) -> HeatmapReport:
    """Render one kx/ky/kz heat map per layer of *csv_path* into *out_dir*."""
    opts = options or HeatmapOptions()
    data = read_materialmap(csv_path)
    wanted = None if layers is None else list(dict.fromkeys(layers))
    if wanted is not None:
        known = {series.name for series in data.series}
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise VisualizeError(
                f"{csv_path}: no such layer(s): {', '.join(unknown)}")
        selected = [series for series in data.series if series.name in set(wanted)]
    else:
        selected = list(data.series)
    if not selected:
        raise VisualizeError(f"{csv_path}: no layer to draw")

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
        cols = array("i", (data.x_interval_of_column[i] for i in series.i))
        rows = array("i", (data.y_interval_of_row[j] for j in series.j))
        images.append(render_layer_heatmap(
            series, cols, rows, data, series_bounds, path,
            index=position, count=len(selected), options=opts))
    return HeatmapReport(csv_path=csv_path, out_dir=out_dir, data=data,
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
    "body{max-width:1100px;margin:40px auto;padding:0 20px;"
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
        "<p class='note'>每层一张图：材料掩膜（光栅化的黑/白结果，灰=该层在此无计算域）"
        "与该层的 kx/ky/kz 等效热导率热图。数值与单位以 MaterialMap.csv 与其 meta 为准，"
        "本页只便于人眼核对。</p>",
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
            lines.append(f"<img src='{html.escape(entry.mask)}' "
                         f"alt='{html.escape(entry.name)} material mask'>")
        if entry.heatmap:
            lines.append(f"<img src='{html.escape(entry.heatmap)}' "
                         f"alt='{html.escape(entry.name)} kx ky kz heat maps'>")
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
