"""Build a shared XY grid with strict cell-size bounds in millimetres.

Every region boundary is preserved. Each forced interval is subdivided into
as few equal cells as its maximum size permits, provided the minimum size holds. Sampling is checked afterwards. nx/ny describe the result only.
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
        widths = [c.rect.width for c in self.cells]
        heights = [c.rect.height for c in self.cells]
        return {
            "size_constraints_mm": self.size_constraints_mm,
            "actual_edge_range_mm": {
                "x": [min(widths), max(widths)] if widths else None,
                "y": [min(heights), max(heights)] if heights else None},
            "layout": "local-region-grids",
            "nx": self.nx,
            "ny": self.ny,
            "forced_x_line_count": len(self.forced_x),
            "forced_y_line_count": len(self.forced_y),
            "region_count": len(self.rectangles),
            "cell_count": len(self.cells),
            "bounds_mm": None if self.bounds is None else list(self.bounds),
            "x_lines_mm": self.x_lines,
            "y_lines_mm": self.y_lines,
            "cells": [{"region_id":c.region_id,"i":c.i,"j":c.j,**c.rect.as_dict()} for c in self.cells],
        }



def _allocate(lines, minimum, maximum, axis, diagnostics):
    left,right = lines
    length = right-left
    ratio = length/maximum
    if not math.isfinite(ratio) or ratio > 1_000_000:
        diagnostics.error(GRID_SIZE_INCOMPATIBLE,
                          f"{axis} interval [{left:g}, {right:g}] mm requires unrepresentable count {ratio:g}")
        return []
    count = max(1,math.ceil(ratio-1e-10))
    upper = math.floor(length/minimum+1e-10)
    if count > upper:
        diagnostics.error(GRID_SIZE_INCOMPATIBLE,
            f"{axis} interval [{left:g}, {right:g}] mm (length {length:g}) "
            f"cannot satisfy [{minimum:g}, {maximum:g}] mm; profile feature/region boundary must be retained")
        return []
    return [left]+[left+length*i/count for i in range(1,count)]+[right]


def build_grid(rectangles, options, *, diagnostics=None, capacity_x=None, capacity_y=None):
    """Independently subdivide every region, then check sampling capacity."""
    diags = diagnostics if diagnostics is not None else Diagnostics()
    forced_x = sorted({x for r in rectangles for x in (r.x0,r.x1)})
    forced_y = sorted({y for r in rectangles for y in (r.y0,r.y1)})
    planned = []
    x_pairs,y_pairs = set(),set()
    for rid,r in enumerate(rectangles):
        xs = _allocate((r.x0,r.x1),options.min_cell_mm[0],options.max_cell_mm[0],f"X region {rid}",diags)
        ys = _allocate((r.y0,r.y1),options.min_cell_mm[1],options.max_cell_mm[1],f"Y region {rid}",diags)
        if not xs or not ys:
            return BoardGrid([],[],forced_x,forced_y,list(rectangles),[])
        for a,b in zip(xs,xs[1:]):
            for c,d in zip(ys,ys[1:]):
                planned.append((rid,Rect(a,c,b,d)))
                x_pairs.add((a,b)); y_pairs.add((c,d))
    xi={pair:i for i,pair in enumerate(sorted(x_pairs))}
    yi={pair:j for j,pair in enumerate(sorted(y_pairs))}
    cells=[Cell(xi[(r.x0,r.x1)],yi[(r.y0,r.y1)],r,rid) for rid,r in planned]
    grid=BoardGrid(sorted({x for p in x_pairs for x in p}),sorted({y for p in y_pairs for y in p}),
                   forced_x,forced_y,list(rectangles),cells,
                   size_constraints_mm={"min":list(options.min_cell_mm),"max":list(options.max_cell_mm)})
    for c in cells: grid.cells_by_band.setdefault(c.j,[]).append(c)
    for cell in cells:
        for axis,lo,hi,capacity in (("X",cell.rect.x0,cell.rect.x1,capacity_x),
                                    ("Y",cell.rect.y0,cell.rect.y1,capacity_y)):
            if capacity is not None and capacity(lo,hi)<1:
                diags.error(GRID_CELL_WITHOUT_PIXEL,
                    f"{axis} region {cell.region_id} interval [{lo:g}, {hi:g}] mm has no pixel centre; increase px/py")
                # The coordinates were already planned; never adapt geometry.
                return grid
    if not cells: diags.error(GRID_NO_BOARD_CELLS,"no modeled board cells")
    return grid
