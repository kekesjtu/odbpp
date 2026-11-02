"""Independent uniform Cartesian grids within each physical region, in mm."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence
from functools import cached_property

from .diagnostics import (
    GRID_RESOURCE_LIMIT,
    GRID_CELL_WITHOUT_PIXEL,
    GRID_NO_BOARD_CELLS,
    Diagnostics,
)
from .rectify import Rect


__all__ = ["GridOptions", "Cell", "BoardGrid", "build_grid"]


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
    max_cell_mm: float | Sequence[float] = DEFAULT_MAX_CELL_MM
    mesh_preset: str | None = None
    auto_raster: bool = False

    def __post_init__(self) -> None:
        if self.mesh_preset not in (None, "coarse", "standard", "fine"):
            raise ValueError("mesh preset must be coarse, standard or fine")
        if self.px < 1 or self.py < 1:
            raise ValueError(f"pixel counts must be positive, got {self.px}x{self.py}")
        high = size_pair(self.max_cell_mm)
        for axis, hi in zip("XY", high):
            if not math.isfinite(hi) or hi <= 0:
                raise ValueError(f"{axis} maximum cell size must be finite and positive, got {hi} mm")
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



MAX_AXIS_CELLS = 1_000_000
MAX_GRID_CELLS = 5_000_000


def uniform_lines(low, high, count):
    """Shared arithmetic for planning and count-based reconstruction."""
    return [low]+[low+(high-low)*i/count for i in range(1,count)]+[high]


@dataclass(frozen=True)
class RegionGrid:
    region_id: int
    rect: Rect
    nx: int
    ny: int

    @cached_property
    def x_lines(self):
        return uniform_lines(self.rect.x0,self.rect.x1,self.nx)

    @cached_property
    def y_lines(self):
        return uniform_lines(self.rect.y0,self.rect.y1,self.ny)

    def as_dict(self):
        return {"region_id":self.region_id,
                "bounds_mm":{"x":[self.rect.x0,self.rect.x1],"y":[self.rect.y0,self.rect.y1]},
                "grid_counts":{"x":self.nx,"y":self.ny,"z":1}}


@dataclass
class BoardGrid:
    regions: list[RegionGrid]
    rectangles: list[Rect]
    cells: list[Cell]
    size_constraints_mm: dict | None = None
    mesh_configuration: dict | None = None

    @property
    def bounds(self):
        if not self.rectangles:
            return None
        return (min(r.x0 for r in self.rectangles),min(r.y0 for r in self.rectangles),
                max(r.x1 for r in self.rectangles),max(r.y1 for r in self.rectangles))

    def as_dict(self):
        widths=[c.rect.width for c in self.cells]
        heights=[c.rect.height for c in self.cells]
        return {"layout":"region-local-cartesian","conformity_required":False,
                "size_constraints_mm":self.size_constraints_mm,
                "actual_edge_range_mm":{"x":[min(widths),max(widths)] if widths else None,
                                        "y":[min(heights),max(heights)] if heights else None},
                "mesh_configuration":self.mesh_configuration,
                "region_count":len(self.regions),"cell_count":len(self.cells),
                "bounds_mm":list(self.bounds) if self.bounds else None,
                "regions":[r.as_dict() for r in self.regions],
                "cells":[{"region_id":c.region_id,"i":c.i,"j":c.j,**c.rect.as_dict()} for c in self.cells]}


def _allocate(lines, maximum, axis, diagnostics):
    left,right=lines
    ratio=(right-left)/maximum
    if not math.isfinite(ratio) or ratio>MAX_AXIS_CELLS:
        diagnostics.error(GRID_RESOURCE_LIMIT,
            f"{axis} interval [{left:g}, {right:g}] mm, length={right-left:g}, max={maximum:g}: count={ratio:g}")
        return []
    return uniform_lines(left,right,max(1,math.ceil(ratio)))


def _check_rectangles(rectangles):
    active=[]
    for r in sorted(rectangles,key=lambda r:r.x0):
        if not all(math.isfinite(v) for v in r.bounds) or r.width<=0 or r.height<=0:
            raise ValueError(f"region must have finite positive extent: {r.bounds}")
        active=[p for p in active if p.x1>r.x0]
        if any(min(p.y1,r.y1)>max(p.y0,r.y0) for p in active):
            raise ValueError(f"overlapping regions at {r.bounds}")
        active.append(r)


def build_grid(rectangles, options, *, diagnostics=None, capacity_x=None, capacity_y=None):
    diags=diagnostics if diagnostics is not None else Diagnostics()
    rectangles=list(rectangles)
    _check_rectangles(rectangles)
    regions,cells=[],[]
    for rid,r in enumerate(rectangles):
        xs=_allocate((r.x0,r.x1),options.max_cell_mm[0],f"X region {rid}",diags)
        ys=_allocate((r.y0,r.y1),options.max_cell_mm[1],f"Y region {rid}",diags)
        if not xs or not ys:
            return BoardGrid([],rectangles,[])
        if len(cells)+(len(xs)-1)*(len(ys)-1)>MAX_GRID_CELLS:
            diags.error(GRID_RESOURCE_LIMIT,f"region {rid}: more than {MAX_GRID_CELLS} modeled cells")
            return BoardGrid([],rectangles,[])
        regions.append(RegionGrid(rid,r,len(xs)-1,len(ys)-1))
        cells.extend(Cell(i,j,Rect(a,b,c,d),rid) for j,(b,d) in enumerate(zip(ys,ys[1:]))
                     for i,(a,c) in enumerate(zip(xs,xs[1:])))
    grid=BoardGrid(regions,rectangles,cells,size_constraints_mm={"max":list(options.max_cell_mm)})
    for cell in cells:
        for axis,lo,hi,capacity in (("X",cell.rect.x0,cell.rect.x1,capacity_x),
                                    ("Y",cell.rect.y0,cell.rect.y1,capacity_y)):
            if capacity is not None and capacity(lo,hi)<1:
                diags.error(GRID_CELL_WITHOUT_PIXEL,
                    f"{axis} region {cell.region_id} local cell ({cell.i},{cell.j}) "
                    f"interval [{lo:g}, {hi:g}] mm has no pixel centre; increase px/py")
                return grid
    if not cells:
        diags.error(GRID_NO_BOARD_CELLS,"no modeled board cells")
    return grid
