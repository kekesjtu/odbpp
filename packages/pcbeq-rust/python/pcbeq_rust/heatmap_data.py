"""MaterialMap-only charts owned by the Rust distribution."""
from __future__ import annotations
import math
import os
from array import array
from dataclasses import dataclass, field
from typing import Optional, Sequence
from .errors import EquivalenceErrorBase
from .png import Rgb


class VisualizeError(EquivalenceErrorBase):
    """The input cannot be turned into a picture, and guessing is not an option."""


NO_DATA_COLOUR: Rgb = (232, 236, 241)


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


@dataclass
class LayerSeries:
    """One layer's cells, in JSON order, straight off the file."""

    name: str
    layer_type: str = ""
    z_start_mm: Optional[float] = None
    z_end_mm: Optional[float] = None
    thickness_known: bool = False
    kx: array = field(default_factory=lambda: array("d"))
    ky: array = field(default_factory=lambda: array("d"))
    kz: array = field(default_factory=lambda: array("d"))
    rectangles: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rectangles)

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
    """Physical rectangles per layer; edge sets describe display coordinates."""

    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    series: list[LayerSeries]
    row_count: int = 0
    skipped_invalid: int = 0
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
    # These coordinate sets describe display bounds only; no common lattice is
    # inferred. PolyCollection uses every reconstructed cell's full rectangle.
    xs = tuple(sorted({x for r in document.regions for x in r.axis_lines("x")}))
    ys = tuple(sorted({y for r in document.regions for y in r.axis_lines("y")}))
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
        series.kx.append(values[0]); series.ky.append(values[1]); series.kz.append(values[2])
        count += 1
    return MaterialMapData(x_edges=xs, y_edges=ys, series=list(layers.values()),
        row_count=count, skipped_invalid=0)


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
