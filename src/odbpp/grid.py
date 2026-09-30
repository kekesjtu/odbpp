"""Build a shared XY grid with strict cell-size bounds in millimetres.

Every region boundary is preserved. Each forced interval is subdivided into
as few equal cells as its maximum size permits, provided the minimum size and
raster sampling requirements also hold. nx/ny describe the result only.
"""

from __future__ import annotations

from bisect import bisect_right
import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .diagnostics import (
    GRID_SIZE_INCOMPATIBLE,
    GRID_CELL_WITHOUT_PIXEL,
    GRID_NO_BOARD_CELLS,
    Diagnostics,
)
from .rectify import Rect

__all__ = ["GridOptions", "Cell", "BoardGrid", "build_grid"]


DEFAULT_MIN_CELL_MM = 0.1
DEFAULT_MAX_CELL_MM = 5.0


def size_pair(value: float | Sequence[float]) -> tuple[float, float]:
    """One size for both axes, or an explicit X/Y pair."""
    if isinstance(value, (int, float)):
        return float(value), float(value)
    if len(value) == 1:
        return float(value[0]), float(value[0])
    if len(value) == 2:
        return float(value[0]), float(value[1])
    raise ValueError("cell size needs one value or two values (X Y), in mm")


@dataclass(frozen=True)
class GridOptions:
    px: int
    py: int
    min_cell_mm: float | Sequence[float] = DEFAULT_MIN_CELL_MM
    max_cell_mm: float | Sequence[float] = DEFAULT_MAX_CELL_MM

    def __post_init__(self) -> None:
        if self.px < 1 or self.py < 1:
            raise ValueError(f"pixel counts must be positive, got {self.px}x{self.py}")
        low, high = size_pair(self.min_cell_mm), size_pair(self.max_cell_mm)
        for axis, lo, hi in zip("XY", low, high):
            if not math.isfinite(lo) or not math.isfinite(hi) or not 0 < lo <= hi:
                raise ValueError(f"{axis} cell sizes require finite 0 < min <= max, "
                                 f"got [{lo}, {hi}] mm")
        object.__setattr__(self, "min_cell_mm", low)
        object.__setattr__(self, "max_cell_mm", high)


@dataclass(frozen=True)
class Cell:
    """One board cell: a rectangle that lies inside exactly one region."""

    i: int
    j: int
    rect: Rect
    region_id: int

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.rect.bounds

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.rect.x0 + self.rect.x1) / 2.0,
                (self.rect.y0 + self.rect.y1) / 2.0)


@dataclass
class BoardGrid:
    """The shared XY grid plus the board cells that actually exist."""

    x_lines: list[float]
    y_lines: list[float]
    forced_x: list[float]
    forced_y: list[float]
    rectangles: list[Rect]
    cells: list[Cell]
    #: ``{j: [cell, ...]}`` so a rasteriser can stream one horizontal band.
    cells_by_band: dict[int, list[Cell]] = field(default_factory=dict)
    size_constraints_mm: Optional[dict] = None

    @property
    def nx(self) -> int:
        return max(0, len(self.x_lines) - 1)

    @property
    def ny(self) -> int:
        return max(0, len(self.y_lines) - 1)

    @property
    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        if not self.rectangles:
            return None
        return (min(r.x0 for r in self.rectangles),
                min(r.y0 for r in self.rectangles),
                max(r.x1 for r in self.rectangles),
                max(r.y1 for r in self.rectangles))

    def as_dict(self) -> dict:
        widths = [b-a for a, b in zip(self.x_lines, self.x_lines[1:])]
        heights = [b-a for a, b in zip(self.y_lines, self.y_lines[1:])]
        return {
            "size_constraints_mm": self.size_constraints_mm,
            "actual_edge_range_mm": {
                "x": [min(widths), max(widths)] if widths else None,
                "y": [min(heights), max(heights)] if heights else None},
            "nx": self.nx,
            "ny": self.ny,
            "forced_x_line_count": len(self.forced_x),
            "forced_y_line_count": len(self.forced_y),
            "region_count": len(self.rectangles),
            "cell_count": len(self.cells),
            "bounds_mm": None if self.bounds is None else list(self.bounds),
            "x_lines_mm": self.x_lines,
            "y_lines_mm": self.y_lines,
        }


def _allocate(lines: Sequence[float], minimum: float, maximum: float, axis: str,
              capacity: Optional[Callable[[float, float], int]],
              diagnostics: Diagnostics, pixel_limit: int) -> list[float]:
    """Keep forced lines and enforce both bounds on each interval."""
    if not lines:
        return []
    out = [lines[0]]
    for left, right in zip(lines, lines[1:]):
        length = right - left
        ratio = length / maximum
        available = capacity(left, right) if capacity is not None else pixel_limit
        if not math.isfinite(ratio) or ratio > available + 1e-10:
            diagnostics.error(
                GRID_CELL_WITHOUT_PIXEL,
                f"{axis.upper()} interval [{left:g}, {right:g}] mm needs at least "
                f"{ratio:g} cells for max={maximum:g} mm, but has at most {available} raster "
                "pixel centres; increase px/py or increase max-cell-mm")
            return []
        count = max(1, math.ceil(ratio - 1e-10))
        upper_ratio = length / minimum
        upper = math.floor(upper_ratio + 1e-10) if math.isfinite(upper_ratio) else count
        if count > upper:
            diagnostics.error(
                GRID_SIZE_INCOMPATIBLE,
                f"{axis.upper()} interval [{left:g}, {right:g}] mm (width {length:g}) "
                f"cannot be divided into cells within [{minimum:g}, {maximum:g}] mm; "
                "forced region boundaries cannot be removed")
            return []
        step = length / count
        edges = [left + step * sub for sub in range(1, count)] + [right]
        if capacity is not None:
            for a, b in zip([left] + edges[:-1], edges):
                if capacity(a, b) < 1:
                    diagnostics.error(
                        GRID_CELL_WITHOUT_PIXEL,
                        f"{axis.upper()} interval [{a:g}, {b:g}] mm contains no raster "
                        "pixel centre; increase px/py or increase max-cell-mm")
                    return []
        out.extend(edges)
    return out


def build_grid(
    rectangles: Sequence[Rect],
    options: GridOptions,
    *,
    diagnostics: Optional[Diagnostics] = None,
    capacity_x: Optional[Callable[[float, float], int]] = None,
    capacity_y: Optional[Callable[[float, float], int]] = None,
) -> BoardGrid:
    """Build the shared board grid from *rectangles* and cell-size limits.

    ``capacity_x``/``capacity_y`` report how many raster pixel centres lie in a
    forced interval.  They let the allocator refuse to subdivide an interval past
    the point where its cells would still contain a sample.  Omit them only for a
    grid with no raster, e.g. in a unit test of the geometry alone.
    """
    diags = diagnostics if diagnostics is not None else Diagnostics()

    forced_x = sorted({r.x0 for r in rectangles} | {r.x1 for r in rectangles})
    forced_y = sorted({r.y0 for r in rectangles} | {r.y1 for r in rectangles})

    x_lines = _allocate(forced_x, options.min_cell_mm[0], options.max_cell_mm[0],
                        "x", capacity_x, diags, options.px)
    y_lines = _allocate(forced_y, options.min_cell_mm[1], options.max_cell_mm[1],
                        "y", capacity_y, diags, options.py)
    if not x_lines or not y_lines:
        return BoardGrid([], [], forced_x, forced_y, list(rectangles), [])

    # Which forced slab each x/y cell lies in.  Every grid cell is contained in
    # one slab because the forced boundaries are grid lines.
    def slab_of(line_value: float, forced: Sequence[float]) -> int:
        return max(0, bisect_right(forced, line_value) - 1)

    x_slabs = [slab_of(value, forced_x) for value in x_lines[:-1]]
    y_slabs = [slab_of(value, forced_y) for value in y_lines[:-1]]

    # Map (x slab, y slab) -> region index.  Rectangles are disjoint and their
    # edges are forced lines, so a slab pair is owned by at most one region.
    owner: dict[tuple[int, int], int] = {}
    for region_id, rect in enumerate(rectangles):
        x0 = slab_of(rect.x0, forced_x)
        x1 = slab_of(rect.x1, forced_x)
        y0 = slab_of(rect.y0, forced_y)
        y1 = slab_of(rect.y1, forced_y)
        for sx in range(x0, x1):
            for sy in range(y0, y1):
                owner[(sx, sy)] = region_id

    cells: list[Cell] = []
    cells_by_band: dict[int, list[Cell]] = {}
    for i in range(len(x_lines) - 1):
        sx = x_slabs[i]
        for j in range(len(y_lines) - 1):
            region_id = owner.get((sx, y_slabs[j]))
            if region_id is None:
                continue
            cell = Cell(i=i, j=j,
                        rect=Rect(x_lines[i], y_lines[j],
                                  x_lines[i + 1], y_lines[j + 1]),
                        region_id=region_id)
            cells.append(cell)
            cells_by_band.setdefault(j, []).append(cell)

    if not cells:
        diags.error(GRID_NO_BOARD_CELLS,
                    "the grid contains no cell inside the board outline")

    return BoardGrid(x_lines=x_lines, y_lines=y_lines,
                     forced_x=forced_x, forced_y=forced_y,
                     rectangles=list(rectangles), cells=cells,
                     cells_by_band=cells_by_band,
                     size_constraints_mm={"min": list(options.min_cell_mm),
                                          "max": list(options.max_cell_mm)})
