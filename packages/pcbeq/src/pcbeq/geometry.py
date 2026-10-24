"""Millimetre polygon kernel; no ODB++ records, arcs or symbol library."""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable, Iterator, Optional, Sequence
import pyclipper

Point = tuple[float, float]
SCALE = 1_000_000
_FILL = pyclipper.PFT_NONZERO


def signed_area(points: Sequence[Point]) -> float:
    """Shoelace area; positive for counter-clockwise input.

    A duplicated closing point contributes nothing, so either convention is
    accepted.
    """
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def is_clockwise(points: Sequence[Point]) -> bool:
    return signed_area(points) < 0.0


def bounding_box(points: Iterable[Point]) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)``; raises on an empty input."""
    it = iter(points)
    try:
        x0, y0 = next(it)
    except StopIteration:
        raise ValueError("bounding_box of an empty point set") from None
    min_x = max_x = x0
    min_y = max_y = y0
    for x, y in it:
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)
    return min_x, min_y, max_x, max_y


@dataclass(frozen=True)
class Ring:
    """A closed boundary, wound per spec p.39.

    Points and the ring are immutable, so derived measurements can be reused
    safely. Regions remain mutable and deliberately do not cache aggregates.
    """

    points: tuple[Point, ...]
    #: True when the ring bounds a void inside its containing island.
    is_hole: bool

    def __len__(self) -> int:
        return len(self.points)

    @cached_property
    def area(self) -> float:
        """Absolute area in mm^2."""
        total = 0.0
        pts = self.points
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            total += x1 * y2 - x2 * y1
        return abs(total) * 0.5

    @cached_property
    def bounds(self) -> tuple[float, float, float, float]:
        return bounding_box(self.points)


@dataclass
class Region:
    """A solid area: islands and the holes inside them.

    An empty :class:`Region` is a legitimate result -- a layer that subtracts
    everything, or a symbol with zero size -- so ``bool()`` is the emptiness
    test rather than an error signal.
    """

    rings: list[Ring] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.rings)

    def __iter__(self) -> Iterator[Ring]:
        return iter(self.rings)

    def __len__(self) -> int:
        return len(self.rings)

    @property
    def islands(self) -> list[Ring]:
        return [r for r in self.rings if not r.is_hole]

    @property
    def holes(self) -> list[Ring]:
        return [r for r in self.rings if r.is_hole]

    @property
    def area(self) -> float:
        """Net area in mm^2: islands minus holes."""
        return sum(r.area if not r.is_hole else -r.area for r in self.rings)

    @property
    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        """Overall bounding box, or ``None`` when the region is empty."""
        if not self.rings:
            return None
        boxes = [r.bounds for r in self.rings]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))




def _to_clipper(points: Sequence[Point]) -> list[tuple[int, int]]:
    """Quantise a ring, dropping consecutive duplicates.

    Duplicates are not merely noise: two source points can round to the same
    integer, and Clipper treats a zero-length edge as a degeneracy it has to
    clean up.  Removing them here keeps the operation deterministic.
    """
    out: list[tuple[int, int]] = []
    for x, y in points:
        point = (int(round(x * SCALE)), int(round(y * SCALE)))
        if not out or out[-1] != point:
            out.append(point)
    # Drop a repeated closing point.
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _from_clipper(path: Iterable[tuple[int, int]]) -> list[Point]:
    return [(x / SCALE, y / SCALE) for x, y in path]


def region_from_rings(rings: Iterable[tuple[Sequence[Point], bool]]) -> Region:
    """Build a region from ``(points, is_hole)`` pairs, winding them correctly."""
    out: list[Ring] = []
    for points, is_hole in rings:
        ring = _make_ring(points, is_hole)
        if ring is not None:
            out.append(ring)
    return Region(out)


def _make_ring(points: Sequence[Point], is_hole: bool) -> Optional[Ring]:
    """Quantise, deduplicate and wind a ring; ``None`` if it degenerates."""
    quantised = _to_clipper(points)
    if len(quantised) < 3:
        return None

    # A collinear ring is not a polygon.  Clipper would accept it and later
    # produce a zero-area sliver no consumer can interpret, so it is rejected
    # here where the reason is still known.
    #
    # The test sums the *absolute* cross products rather than the signed shoelace
    # area, because a self-intersecting bowtie has a signed area of exactly zero
    # while enclosing plenty of area -- and repairing bowties is precisely what
    # simplify() is for.
    cross_sum = 0
    for index, (x, y) in enumerate(quantised):
        nx, ny = quantised[(index + 1) % len(quantised)]
        cross_sum += abs(x * ny - nx * y)
    if cross_sum == 0:
        return None

    deduped = _from_clipper(quantised)
    # Spec p.39: islands clockwise, holes counter-clockwise.
    want_clockwise = not is_hole
    if is_clockwise(deduped) != want_clockwise:
        deduped = list(reversed(deduped))
    return Ring(tuple(deduped), is_hole)


def _region_from_tree(node) -> Region:
    """Walk a :class:`~pyclipper.PyPolyNode` tree into a region.

    The tree is what makes hole detection reliable: ``IsHole`` comes from the
    containment structure Clipper computed, so nesting is never re-derived by
    point-in-polygon tests of our own.
    """
    rings: list[Ring] = []

    def walk(parent, depth: int) -> None:
        for child in parent.Childs:
            points = _from_clipper(child.Contour)
            if len(points) >= 3:
                ring = _make_ring(points, is_hole=bool(child.IsHole))
                if ring is not None:
                    rings.append(ring)
            walk(child, depth + 1)
    walk(node, 0)
    return Region(rings)


def _clipper_for(subject: Sequence[Ring], clip: Sequence[Ring] = ()) -> pyclipper.Pyclipper:
    engine = pyclipper.Pyclipper()
    for ring in subject:
        if len(ring.points) >= 3:
            engine.AddPath(_to_clipper(ring.points), pyclipper.PT_SUBJECT, True)
    for ring in clip:
        if len(ring.points) >= 3:
            engine.AddPath(_to_clipper(ring.points), pyclipper.PT_CLIP, True)
    return engine


def union(*regions: Region) -> Region:
    """Union of any number of regions; holes are honoured by NONZERO fill."""
    rings: list[Ring] = []
    for region in regions:
        rings.extend(region.rings)
    if not rings:
        return Region()
    if len(rings) == 1:
        return Region([rings[0]])

    engine = pyclipper.Pyclipper()
    # Every ring is added as a subject: the NONZERO fill rule then does the work,
    # and a hole ring (counter-clockwise) correctly punches through its island
    # because the winding numbers cancel.
    for ring in rings:
        engine.AddPath(_to_clipper(ring.points), pyclipper.PT_SUBJECT, True)

    tree = engine.Execute2(pyclipper.CT_UNION, _FILL, _FILL)
    return _region_from_tree(tree)


def difference(subject: Region, clip: Region) -> Region:
    """``subject`` minus ``clip``.

    The operation ODB++ negative polarity needs: a negative feature erases
    everything beneath it (spec p.39).
    """
    if not subject:
        return Region()
    if not clip:
        return Region(list(subject.rings))
    engine = _clipper_for(subject.rings, clip.rings)
    tree = engine.Execute2(pyclipper.CT_DIFFERENCE, _FILL, _FILL)
    return _region_from_tree(tree)


def intersection(*regions: Region) -> Region:
    """Intersection of *all* given regions.

    Composed pairwise rather than by feeding every later region to the clip
    argument: ``Execute2`` treats all clip paths as one polygon set, so a single
    call would compute ``A & (B | C)`` instead of ``A & B & C``.  An empty
    operand makes the whole intersection empty -- including an empty *first*
    operand, which must not be mistaken for "no constraint".
    """
    if not regions:
        return Region()

    result = Region(list(regions[0].rings))
    if not result:
        return Region()

    for other in regions[1:]:
        if not other:
            return Region()
        engine = _clipper_for(result.rings, other.rings)
        result = _region_from_tree(
            engine.Execute2(pyclipper.CT_INTERSECTION, _FILL, _FILL)
        )
        if not result:
            return Region()
    return result
