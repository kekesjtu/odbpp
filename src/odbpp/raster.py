"""Scanline rasterisation of regions onto the shared pixel raster.

The equivalence formulas are defined on a pixel block (AGENTS 公式推导 2.2), so
the material pattern of a layer has to be sampled at pixel centres.  Doing that
naively -- a point-in-polygon test per pixel per layer -- is both slow and
ambiguous about holes, so this module converts a :class:`~odbpp.clipper.Region`
into a list of boundary edges and answers "which pixels on this scanline are
inside?" with an even-odd crossing count.

Why even-odd is correct here: composed regions are fully positive
(AGENTS constraint 2) and Clipper emits non-overlapping nested rings, so island
and hole nesting alternates.  The fill therefore never depends on the order the
rings happen to be in -- which is exactly the trap AGENTS constraint 6 warns
about.

The raster itself is only ever materialised one pixel row at a time
(``bytearray`` of length ``px``), so a 5000x5000 board at 43 layers does not
need gigabytes of resident masks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .clipper import Region

__all__ = ["RasterAxis", "region_edges", "spans_at", "span_to_pixels"]


Edge = tuple[float, float, float, float]


def region_edges(region: Region) -> list[Edge]:
    """Boundary edges of *region* as ``(x1, y1, x2, y2)`` tuples."""
    edges: list[Edge] = []
    for ring in region.rings:
        points = ring.points
        count = len(points)
        for index in range(count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % count]
            edges.append((x1, y1, x2, y2))
    return edges


def spans_at(edges: Sequence[Edge], y: float) -> list[tuple[float, float]]:
    """Filled ``x`` intervals where the horizontal line at *y* crosses *edges*.

    Even-odd: sort the crossings and pair them.  A vertex lying exactly on the
    scanline is handled by the half-open comparison ``(y1 <= y) != (y2 <= y)``,
    which counts a shared endpoint once instead of twice.
    """
    crossings: list[float] = []
    for x1, y1, x2, y2 in edges:
        if (y1 <= y) == (y2 <= y):
            continue
        t = (y - y1) / (y2 - y1)
        crossings.append(x1 + (x2 - x1) * t)
    if len(crossings) < 2:
        return []
    crossings.sort()
    spans: list[tuple[float, float]] = []
    for index in range(0, len(crossings) - 1, 2):
        left, right = crossings[index], crossings[index + 1]
        if right > left:
            spans.append((left, right))
    return spans


def span_to_pixels(span: tuple[float, float], axis: "RasterAxis",
                   limit: Optional[int] = None) -> tuple[int, int]:
    """Pixel index range whose centres lie in *span*.

    A pixel centre is ``origin + (a + 0.5) * step``; the half-open interval
    ``[left, right)`` therefore selects ``a`` in
    ``[ceil((left-origin)/step - 0.5), ceil((right-origin)/step - 0.5))``.
    """
    return (axis.pixel_index(span[0]), axis.pixel_index(span[1]))


@dataclass(frozen=True)
class RasterAxis:
    """One axis of the base raster: ``count`` pixels of ``step`` mm."""

    origin: float
    step: float
    count: int

    def __post_init__(self) -> None:
        if self.step <= 0.0:
            raise ValueError(f"pixel step must be positive, got {self.step!r}")
        if self.count < 1:
            raise ValueError(f"pixel count must be positive, got {self.count!r}")

    def centre(self, index: int) -> float:
        return self.origin + (index + 0.5) * self.step

    def pixel_index(self, value: float) -> int:
        """First pixel index whose centre is ``>= value`` (clamped to the axis)."""
        raw = math.ceil((value - self.origin) / self.step - 0.5 - 1e-9)
        return max(0, min(self.count, int(raw)))

    def range_for(self, low: float, high: float) -> tuple[int, int]:
        """Pixel index range ``[a0, a1)`` for the half-open box ``[low, high)``."""
        return (self.pixel_index(low), self.pixel_index(high))

    def as_dict(self) -> dict:
        return {"origin_mm": self.origin, "step_mm": self.step,
                "count": self.count}


def fill_row(spans: Iterable[tuple[float, float]], axis: RasterAxis,
             row: bytearray) -> None:
    """Set the bytes of *row* whose pixel centres fall inside *spans*."""
    for span in spans:
        a0, a1 = span_to_pixels(span, axis)
        if a1 > a0:
            row[a0:a1] = b"\x01" * (a1 - a0)
