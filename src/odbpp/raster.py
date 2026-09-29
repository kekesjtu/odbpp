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
from typing import Iterable, Iterator, Optional, Sequence

from .clipper import Region

__all__ = ["RasterAxis", "LayerMask", "region_edges", "spans_at",
           "span_to_pixels"]


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


class LayerMask:
    """One layer's black/white raster at the base pixel resolution.

    This is the picture the equivalence formulas integrate over, kept so a
    reviewer can *look* at what produced the numbers instead of inferring it
    from the CSV.  Three tones, one byte each:

    * :attr:`BLACK` (0) -- the copper/drill phase, material 1;
    * :attr:`WHITE` (255) -- base laminate, material 2, inside the layer's
      computational domain;
    * :attr:`OUTSIDE` (160) -- no computational domain here: board-outside or
      void.  It is *not* a third material; AGENTS constraint 7 forbids counting
      it as base laminate, and the equivalence never does.

    Rows are stored bottom-up (``row * px + column``, row 0 at the smallest
    board Y) because that is the order the scanline visits them;
    :meth:`top_down_rows` flips the picture for the PNG, whose first row is the
    top of the image.  Rows are filled from scanline spans with slice assignment,
    so building the mask stays at C speed even for a multi-megapixel board.
    """

    BLACK = 0
    WHITE = 255
    OUTSIDE = 160

    def __init__(self, px: int, py: int) -> None:
        if px < 1 or py < 1:
            raise ValueError(f"mask size must be positive, got {px}x{py}")
        self.px = int(px)
        self.py = int(py)
        self.pixels = bytearray([self.OUTSIDE]) * (self.px * self.py)

    def set_spans(self, row: int,
                  domain_spans: Iterable[tuple[float, float]],
                  black_spans: Iterable[tuple[float, float]],
                  axis: RasterAxis) -> None:
        """Fill one pixel row from the domain and black-phase scanline spans."""
        if not 0 <= row < self.py:
            raise IndexError(f"row {row} outside the {self.py}-row mask")
        base = row * self.px
        pixels = self.pixels
        for spans, value in ((domain_spans, self.WHITE), (black_spans, self.BLACK)):
            fill = bytes([value])
            for span in spans:
                a0, a1 = span_to_pixels(span, axis)
                if a1 > a0:
                    pixels[base + a0:base + a1] = fill * (a1 - a0)

    def top_down_rows(self, scale: int = 1) -> Iterator[bytes]:
        """Yield rows for a PNG: top of the board first, pixels replicated.

        *scale* is a nearest-neighbour zoom so a small raster is still viewable;
        it never interpolates, because the mask is a sampling result.
        """
        if scale < 1:
            raise ValueError(f"mask scale must be positive, got {scale}")
        px = self.px
        pixels = self.pixels
        for row in range(self.py - 1, -1, -1):
            line = bytes(pixels[row * px:(row + 1) * px])
            if scale == 1:
                yield line
                continue
            widened = bytearray(px * scale)
            for offset in range(scale):
                widened[offset::scale] = line
            widened_bytes = bytes(widened)
            for _ in range(scale):
                yield widened_bytes

    def resampled_rows(self, width: int, height: int) -> Iterator[bytes]:
        """Yield *height* rows of *width* bytes, top of the board first.

        Nearest neighbour, no interpolation, but the two axes are resampled
        independently: the raster counts pixels along X and Y with different
        physical pitches, so replicating both by the same integer would stretch
        a 163 x 43 mm board into a square.  Source rows are cached because
        upsampling makes several output rows share one.
        """
        if width < 1 or height < 1:
            raise ValueError(
                f"image size must be positive, got {width}x{height}")
        px, py = self.px, self.py
        pixels = self.pixels
        # (source column, how many output columns it fills), in order.
        runs: list[tuple[int, int]] = []
        for index in range(width):
            source = (index * px) // width
            if runs and runs[-1][0] == source:
                runs[-1] = (source, runs[-1][1] + 1)
            else:
                runs.append((source, 1))
        cache: dict[int, bytes] = {}
        for row in range(height):
            source = (row * py) // height
            line = cache.get(source)
            if line is None:
                start = (py - 1 - source) * px
                source_line = pixels[start:start + px]
                if width == px:
                    line = bytes(source_line)
                else:
                    out = bytearray(width)
                    offset = 0
                    for column, run in runs:
                        out[offset:offset + run] = source_line[column:column + 1] * run
                        offset += run
                    line = bytes(out)
                cache[source] = line
            yield line

    def tone_counts(self) -> dict[str, int]:
        """Pixel count per tone, for reporting and for tests."""
        return {
            "black": self.pixels.count(self.BLACK),
            "white": self.pixels.count(self.WHITE),
            "outside": self.pixels.count(self.OUTSIDE),
        }
