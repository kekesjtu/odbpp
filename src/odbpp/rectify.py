"""Rectangularise the board outline into axis-aligned, non-overlapping regions.

AGENTS constraint (外轮廓矩形化与网格) 1-2.  The material-equivalence grid is
composed of axis-aligned cells, so the board outline is first approximated by a
set of axis-aligned rectangles -- a coarser staircase along any diagonal, arc or
curved edge.  This module owns that step and, just as importantly, owns the
*evidence* that the approximation did not quietly throw the board away.

How it works
------------
The outline is decomposed by vertical slabs.  Every distinct ``x`` of a profile
vertex (snapped to the configured tolerance grid) becomes a slab boundary, and
inside a slab the boundary edges are straight, so the covered ``y`` intervals do
not change.  Sampling one ``x`` in the middle of each slab and pairing the
crossings with the even-odd rule therefore yields exact rectangles for a
rectilinear outline and a snapped staircase for a curved one.  Slabs with equal
interval lists are merged, which keeps a rectangular board at one rectangle.

Why the checks are not optional
-------------------------------
A tolerance coarse enough to be useful can also erase a narrow neck or fill a
narrow slot.  Net area is not enough to detect that: a lost island and a filled
hole cancel out.  So the result carries the *lost* and *gained* areas computed
separately with real booleans, and each original island and hole is checked
individually:

* an island the rectangles do not cover is a lost solid -> error;
* a hole the rectangles cover almost completely is a filled void -> error;
* total lost or gained area beyond the tolerance -> error.

The caller decides whether to abort; :class:`RectifyResult` records everything
needed to explain the choice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .clipper import Region, Ring, difference, intersection, union
from .diagnostics import (
    RECTIFY_AREA_ERROR,
    RECTIFY_EMPTY_PROFILE,
    RECTIFY_HOLE_FILLED,
    RECTIFY_ISLAND_LOST,
    RECTIFY_NO_RECTANGLES,
    Diagnostics,
)

__all__ = ["Rect", "RectifyOptions", "RectifyResult", "rectify_region"]


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle in millimetres, ``[x0, x1] x [y0, y1]``."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def as_dict(self) -> dict:
        return {"x0_mm": self.x0, "y0_mm": self.y0,
                "x1_mm": self.x1, "y1_mm": self.y1}

    def ring_points(self) -> list[tuple[float, float]]:
        return [(self.x0, self.y0), (self.x1, self.y0),
                (self.x1, self.y1), (self.x0, self.y1)]


@dataclass(frozen=True)
class RectifyOptions:
    """Configuration for :func:`rectify_region`.

    ``tolerance_mm`` is the snapping grid: every rectangle boundary lies on a
    multiple of it, so the staircase error is bounded by roughly one tolerance
    per sloped edge.  The default of 0.1 mm is deliberately coarser than the
    0.005 mm arc tessellation -- the grid is a *meshing* aid, not artwork -- and
    fine enough that a typical 50 mm board needs no more than a few hundred
    forced lines.  It is configurable because the right value depends on the
    board and the requested ``nx``/``ny``.
    """

    tolerance_mm: float = 0.2
    #: Reject the result when lost or gained area exceeds this fraction of the
    #: original outline area.
    max_relative_area_error: float = 0.01
    #: ... or this many square millimetres, whichever is larger.  The absolute
    #: floor keeps a tiny board from failing on a rounding-scale difference.
    max_absolute_area_error_mm2: float = 0.5

    def __post_init__(self) -> None:
        if self.tolerance_mm <= 0.0:
            raise ValueError(f"tolerance_mm must be positive, got {self.tolerance_mm!r}")
        if self.max_relative_area_error < 0.0:
            raise ValueError("max_relative_area_error cannot be negative")
        if self.max_absolute_area_error_mm2 < 0.0:
            raise ValueError("max_absolute_area_error_mm2 cannot be negative")


@dataclass
class RectifyResult:
    """Rectangles plus the evidence that the outline was preserved."""

    rectangles: list[Rect]
    tolerance_mm: float
    original_area_mm2: float
    rectified_area_mm2: float
    lost_area_mm2: float
    gained_area_mm2: float
    relative_error: float
    ok: bool
    #: ``rectified - original`` (signed) and its magnitude: the classic area
    #: error, which a staircase tends to keep small.
    net_area_error_mm2: float = 0.0
    #: ``lost + gained``: how much of the outline's *shape* moved.  This is the
    #: conservative metric the error check uses, because a lost island and a
    #: filled hole can cancel in the net area while both being real defects.
    shape_deviation_mm2: float = 0.0
    #: Populated by :func:`rectify_region`; the returned region is the rectangles
    #: re-unioned with the nonzero fill rule, so it is a valid :class:`Region`.
    region: Region = field(default_factory=Region)

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
            "tolerance_mm": self.tolerance_mm,
            "rectangle_count": len(self.rectangles),
            "original_area_mm2": self.original_area_mm2,
            "rectified_area_mm2": self.rectified_area_mm2,
            "net_area_error_mm2": self.net_area_error_mm2,
            "shape_deviation_mm2": self.shape_deviation_mm2,
            "lost_area_mm2": self.lost_area_mm2,
            "gained_area_mm2": self.gained_area_mm2,
            "relative_area_error": self.relative_error,
            "ok": self.ok,
            "rectangles": [r.as_dict() for r in self.rectangles],
        }


def _snap_key(value: float, tolerance: float) -> int:
    return int(round(value / tolerance))


def _axis_breaks(rings: Sequence[Ring], tolerance: float) -> list[int]:
    keys = {_snap_key(x, tolerance) for ring in rings for x, _y in ring.points}
    return sorted(keys)


def _crossings(rings: Sequence[Ring], x_mid: float) -> list[float]:
    """``y`` values where the boundary crosses the vertical line ``x = x_mid``.

    Even-odd crossing counting.  ``x_mid`` is chosen strictly inside a slab, so
    no vertex lies on it and an edge either straddles it or does not; a
    horizontal edge that straddles it contributes exactly one crossing, which is
    what pairing the sorted values requires.
    """
    ys: list[float] = []
    for ring in rings:
        points = ring.points
        count = len(points)
        for index in range(count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % count]
            if (x1 <= x_mid) == (x2 <= x_mid):
                continue
            t = (x_mid - x1) / (x2 - x1)
            ys.append(y1 + (y2 - y1) * t)
    ys.sort()
    return ys


def _intervals(crossings: Sequence[float], tolerance: float) -> Optional[list[tuple[int, int]]]:
    """Snapped, deduplicated ``y`` intervals from an even number of crossings."""
    keys: list[int] = []
    for y in crossings:
        key = _snap_key(y, tolerance)
        if not keys or keys[-1] != key:
            keys.append(key)
    if len(keys) % 2 != 0:
        return None
    out: list[tuple[int, int]] = []
    for index in range(0, len(keys), 2):
        y0, y1 = keys[index], keys[index + 1]
        if y1 > y0:
            out.append((y0, y1))
    return out


def _bands(region: Region, tolerance: float) -> list[tuple[float, float, list[tuple[int, int]]]]:
    """Slabs of constant cross-section, subdividing slanted ones.

    Vertex ``x`` values alone give an exact decomposition of a rectilinear
    outline and a single giant step over a long slanted edge.  A single step
    preserves net area (the trapezoid rule) but the staircase no longer follows
    the edge, so the *shape* is wrong even though the area is right.  A slab
    whose interval list differs at its two ends is therefore subdivided into
    steps no wider than the tolerance; rectilinear slabs are left whole.
    """
    breaks = _axis_breaks(region.rings, tolerance)
    bands: list[tuple[float, float, list[tuple[int, int]]]] = []
    for left, right in zip(breaks, breaks[1:]):
        if right <= left:
            continue
        x0 = left * tolerance
        x1 = right * tolerance
        width = x1 - x0
        inset = min(width, tolerance) * 1e-3
        at_left = _intervals(_crossings(region.rings, x0 + inset), tolerance)
        at_right = _intervals(_crossings(region.rings, x1 - inset), tolerance)
        if at_left is not None and at_left == at_right:
            if at_left:
                bands.append((x0, x1, at_left))
            continue
        count = max(1, int(math.ceil(width / tolerance - 1e-9)))
        step = width / count
        for index in range(count):
            xa = x0 + step * index
            xb = xa + step
            intervals = _intervals(
                _crossings(region.rings, (xa + xb) / 2.0), tolerance)
            if intervals:
                bands.append((xa, xb, intervals))
    return bands


def rectify_region(
    region: Region,
    options: Optional[RectifyOptions] = None,
    *,
    diagnostics: Optional[Diagnostics] = None,
) -> RectifyResult:
    """Approximate *region* with axis-aligned rectangles and audit the error.

    The result is never silently lossy: islands lost, holes filled and total
    area error are all reported through *diagnostics* with stable codes, and
    :attr:`RectifyResult.ok` is false when any of them fired.
    """
    opts = options or RectifyOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()
    original_area = region.area

    if not region.rings:
        diags.error(RECTIFY_EMPTY_PROFILE,
                    "the board outline is empty; there is nothing to rectangularise")
        return RectifyResult([], opts.tolerance_mm, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    bands = _bands(region, opts.tolerance_mm)

    # Merge slabs that carry the same interval list into one rectangle column.
    merged: list[tuple[float, float, list[tuple[int, int]]]] = []
    for x0, x1, intervals in bands:
        if merged and merged[-1][2] == intervals:
            merged[-1] = (merged[-1][0], x1, intervals)
        else:
            merged.append((x0, x1, intervals))

    rectangles: list[Rect] = []
    for x0, x1, intervals in merged:
        for y0, y1 in intervals:
            rectangles.append(Rect(x0, y0 * opts.tolerance_mm,
                                   x1, y1 * opts.tolerance_mm))

    if not rectangles:
        diags.error(RECTIFY_NO_RECTANGLES,
                    f"the board outline ({original_area:.3f} mm2) did not "
                    f"survive rectangularisation at {opts.tolerance_mm:g} mm "
                    f"tolerance; no region was produced")
        return RectifyResult([], opts.tolerance_mm, original_area, 0.0,
                             original_area, 0.0, 1.0, False)

    rectified = union(Region([Ring(tuple(r.ring_points()), False) for r in rectangles]))
    rectified_area = rectified.area

    lost = difference(region, rectified)
    gained = difference(rectified, region)
    lost_area = lost.area
    gained_area = gained.area
    net_error = rectified_area - original_area
    shape_deviation = abs(lost_area) + abs(gained_area)
    relative = shape_deviation / original_area if original_area else 0.0
    allowed = max(opts.max_absolute_area_error_mm2,
                  opts.max_relative_area_error * original_area)
    ok = True

    if shape_deviation > allowed:
        ok = False
        diags.error(
            RECTIFY_AREA_ERROR,
            f"rectangularising at {opts.tolerance_mm:g} mm moved more of the "
            f"board outline than allowed: shape deviation {shape_deviation:.4f} "
            f"mm2 (lost {lost_area:.4f}, gained {gained_area:.4f}), net area "
            f"error {net_error:+.4f} mm2, allowed {allowed:.4f} mm2 of "
            f"{original_area:.3f} mm2; use a finer tolerance_mm or accept that "
            f"the grid cannot follow the outline",
        )

    # Per-ring checks catch a narrow feature even when loss and gain cancel.
    for index, ring in enumerate(region.islands):
        if ring.area <= 0.0:
            continue
        covered = intersection(rectified, Region([ring])).area
        if covered < 0.5 * ring.area:
            ok = False
            diags.error(
                RECTIFY_ISLAND_LOST,
                f"solid island #{index} ({ring.area:.4f} mm2, bounds "
                f"{ring.bounds}) is not represented by the rectangularisation "
                f"at {opts.tolerance_mm:g} mm tolerance; it is narrower than "
                f"the tolerance",
            )
    for index, ring in enumerate(region.holes):
        if ring.area <= 0.0:
            continue
        covered = intersection(rectified, Region([ring])).area
        if covered > 0.99 * ring.area:
            ok = False
            diags.error(
                RECTIFY_HOLE_FILLED,
                f"void hole #{index} ({ring.area:.4f} mm2, bounds "
                f"{ring.bounds}) is filled by the rectangularisation at "
                f"{opts.tolerance_mm:g} mm tolerance; it is narrower than the "
                f"tolerance",
            )

    return RectifyResult(
        rectangles=rectangles,
        tolerance_mm=opts.tolerance_mm,
        original_area_mm2=original_area,
        rectified_area_mm2=rectified_area,
        lost_area_mm2=lost_area,
        gained_area_mm2=gained_area,
        relative_error=relative,
        ok=ok,
        region=rectified,
        net_area_error_mm2=net_error,
        shape_deviation_mm2=shape_deviation,
    )
