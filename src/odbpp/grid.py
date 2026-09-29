"""Allocate the board-wide XY grid over the rectangularised outline.

AGENTS constraint (外轮廓矩形化与网格) 3-5.  One grid is shared by every
physical layer.  It is built from the region rectangles produced by
:mod:`odbpp.rectify`:

* every region boundary is a **forced grid line**, so no cell ever straddles
  two regions;
* ``nx``/``ny`` are the *board-wide* cell counts, not per-region counts.  The
  cells left after the forced lines are distributed across the forced intervals
  in proportion to their width;
* a forced line count that does not fit in ``nx``/``ny`` is an input/config
  mismatch and is reported, never absorbed by silently adding cells;
* cells outside every region belong to no material layer ("板外不生成物性网格").

The grid is only a partition; which cells actually carry a layer is decided by
that layer's own computational domain during rasterisation.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .diagnostics import (
    GRID_FORCED_LINES_OVERFLOW,
    GRID_NO_BOARD_CELLS,
    Diagnostics,
)
from .rectify import Rect

__all__ = ["GridOptions", "Cell", "BoardGrid", "build_grid"]


@dataclass(frozen=True)
class GridOptions:
    """Board-wide cell counts.

    ``nx``/``ny`` default to the raster pixel counts when the caller leaves them
    unset (AGENTS 等效器输入与材料规则 3), so the common case is "one cell per
    pixel".  Explicit values must satisfy ``1 <= nx <= px`` and ``1 <= ny <= py``.
    """

    px: int
    py: int
    nx: Optional[int] = None
    ny: Optional[int] = None

    def __post_init__(self) -> None:
        if self.px < 1 or self.py < 1:
            raise ValueError(f"pixel counts must be positive, got {self.px}x{self.py}")
        if self.nx is not None and not 1 <= self.nx <= self.px:
            raise ValueError(
                f"nx={self.nx} must satisfy 1 <= nx <= px={self.px}")
        if self.ny is not None and not 1 <= self.ny <= self.py:
            raise ValueError(
                f"ny={self.ny} must satisfy 1 <= ny <= py={self.py}")

    @property
    def cells_x(self) -> int:
        return self.nx if self.nx is not None else self.px

    @property
    def cells_y(self) -> int:
        return self.ny if self.ny is not None else self.py


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
        return {
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


def _allocate(lines: Sequence[float], total: int, axis: str,
              capacities: Optional[Sequence[int]],
              diagnostics: Diagnostics) -> list[float]:
    """Insert ``total - (len(lines) - 1)`` interior lines between forced lines.

    Each forced interval keeps at least one cell, so its width can never be
    silently zeroed.  The remaining cells are distributed with the
    largest-remainder method, weighted by how many raster pixels each interval
    still has spare.  Weighting by *spare pixels* rather than by width is what
    keeps every cell on a raster sample: an interval with room for one cell must
    not be asked to hold three, because a cell with no pixel centre inside it is
    an error (AGENTS constraint 5), not a rounding detail.
    """
    intervals = len(lines) - 1
    if not lines:
        return []
    remaining = total - intervals
    if remaining < 0:
        diagnostics.error(
            GRID_FORCED_LINES_OVERFLOW,
            f"{_axis_name(axis)} has {len(lines)} forced region boundaries, "
            f"which need at least {intervals} cells, but only {total} "
            f"{axis}-cells were requested; the rectangularisation and the "
            f"requested grid count are incompatible",
        )
        return []
    if remaining == 0:
        return list(lines)

    if capacities is None:
        widths = [lines[k + 1] - lines[k] for k in range(intervals)]
        weights = [max(width, 0.0) for width in widths]
    else:
        # Capacity is the number of pixel centres in each forced interval; a cell
        # gets at least one of them.
        weights = [max(int(capacities[k]) - 1, 0) for k in range(intervals)]
    total_weight = float(sum(weights)) or float(intervals)
    if total_weight <= 0.0:
        weights = [1.0] * intervals
        total_weight = float(intervals)

    exact = [weight / total_weight * remaining for weight in weights]
    shares = [int(value) for value in exact]
    left = remaining - sum(shares)
    order = sorted(range(intervals), key=lambda k: (-(exact[k] - shares[k]), k))
    for k in order[:left]:
        shares[k] += 1

    out: list[float] = [lines[0]]
    for k in range(intervals):
        count = shares[k] + 1
        step = (lines[k + 1] - lines[k]) / count
        for sub in range(1, count):
            out.append(lines[k] + step * sub)
        out.append(lines[k + 1])
    return out


def _axis_name(axis: str) -> str:
    return "X" if axis == "x" else "Y"


def build_grid(
    rectangles: Sequence[Rect],
    options: GridOptions,
    *,
    diagnostics: Optional[Diagnostics] = None,
    capacity_x: Optional[Callable[[float, float], int]] = None,
    capacity_y: Optional[Callable[[float, float], int]] = None,
) -> BoardGrid:
    """Build the shared board grid from *rectangles* and the cell counts.

    ``capacity_x``/``capacity_y`` report how many raster pixel centres lie in a
    forced interval.  They let the allocator refuse to subdivide an interval past
    the point where its cells would still contain a sample.  Omit them only for a
    grid with no raster, e.g. in a unit test of the geometry alone.
    """
    diags = diagnostics if diagnostics is not None else Diagnostics()

    forced_x = sorted({r.x0 for r in rectangles} | {r.x1 for r in rectangles})
    forced_y = sorted({r.y0 for r in rectangles} | {r.y1 for r in rectangles})

    caps_x = None if capacity_x is None else [
        capacity_x(forced_x[k], forced_x[k + 1]) for k in range(len(forced_x) - 1)
    ]
    caps_y = None if capacity_y is None else [
        capacity_y(forced_y[k], forced_y[k + 1]) for k in range(len(forced_y) - 1)
    ]

    x_lines = _allocate(forced_x, options.cells_x, "x", caps_x, diags)
    y_lines = _allocate(forced_y, options.cells_y, "y", caps_y, diags)
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
                     cells_by_band=cells_by_band)
