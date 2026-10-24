"""Planar boolean operations, on top of pyclipper.

Everything that turns ODB++ features into *solid regions* goes through here:
union, difference, intersection, and the Minkowski sweep used to draw lines and
arcs.  Two things about this layer are worth stating up front, because getting
either wrong produces geometry that looks plausible and is wrong.

**Integer quantisation.**  Clipper is an integer library.  Millimetre
coordinates are scaled by :data:`SCALE` (1e-6 mm, one nanometre) on the way in and
divided back out on the way out.  That is far finer than any fabrication
tolerance -- a PCB feature is at least 50 um -- and coarse enough that a 2 m
panel still fits comfortably in Clipper's 64-bit coordinates.  Note that the
source can carry more digits than this (an 8-decimal inch coordinate is 25 nm),
so the scale is a deliberate floor on precision rather than a lossless mapping.

**Orientation is assigned, not inherited.**  Clipper's own winding convention is
an implementation detail that has changed between versions.  Rather than trust
it, every entry point here walks the result as a :class:`~pyclipper.PyPolyNode`
tree -- which states outright whether a ring is a hole -- and then winds the
rings to the ODB++ convention from spec p.39: islands clockwise, holes
counter-clockwise.  Downstream code can therefore treat orientation as data.

The working representation is :class:`Region`: a set of rings that together
describe a solid area, as opposed to :class:`~pcbkit.model.SurfaceGeometry`, which
is the arc-preserving representation the parser produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable, Iterator, Optional, Sequence

import pyclipper

from .geometry import TessellationOptions, bounding_box, is_clockwise
from .model import ContourPart, Point, Polygon, SurfaceGeometry

__all__ = [
    "SCALE",
    "Ring",
    "Region",
    "region_from_geometry",
    "region_from_rings",
    "to_surface_geometry",
    "union",
    "difference",
    "intersection",
    "simplify",
    "snap_to_grid",
    "sweep",
    "sweep_polyline",
    "sweep_paths",
    "stroke_path",
    "stroke_paths",
    "offset",
]

#: Millimetres per integer unit: 1e-6 mm (1 nm).
SCALE = 1_000_000

#: Clipper's "no fill rule for the clip" sentinel is the same as NONZERO here.
_FILL = pyclipper.PFT_NONZERO


# ---------------------------------------------------------------------------
# representation
# ---------------------------------------------------------------------------

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

    def to_surface_geometry(self) -> SurfaceGeometry:
        """Convert to the arc-free :class:`~pcbkit.model.SurfaceGeometry` form."""
        return to_surface_geometry(self)


# ---------------------------------------------------------------------------
# conversion helpers
# ---------------------------------------------------------------------------

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


def to_surface_geometry(region: Region) -> SurfaceGeometry:
    """Build a :class:`~pcbkit.model.SurfaceGeometry` from a region.

    Rings are emitted as pure segments: by this point the arcs have been through
    a boolean operation and no longer exist as arcs, so pretending otherwise
    would be a lie about the data.
    """
    geometry = SurfaceGeometry()
    for ring in region.rings:
        if len(ring.points) < 3:
            continue
        start = ring.points[0]
        geometry.polygons.append(Polygon(
            is_island=not ring.is_hole,
            start=(start[0], start[1]),
            parts=[ContourPart(kind="segment", end=(x, y))
                   for x, y in ring.points[1:]],
        ))
    return geometry


def region_from_rings(rings: Iterable[tuple[Sequence[Point], bool]]) -> Region:
    """Build a region from ``(points, is_hole)`` pairs, winding them correctly."""
    out: list[Ring] = []
    for points, is_hole in rings:
        ring = _make_ring(points, is_hole)
        if ring is not None:
            out.append(ring)
    return Region(out)


def region_from_geometry(geometry: SurfaceGeometry,
                         options=None) -> Region:
    """Convert a :class:`~pcbkit.model.SurfaceGeometry` into a region.

    Arcs are tessellated here, because booleans cannot carry them.  The
    tessellation tolerance is therefore the last point at which curve fidelity
    can be chosen.
    """
    from .geometry import polygon_points

    opts = options or TessellationOptions()
    # Flatten all polygons first, preserving validation/exception ordering,
    # but do not construct and immediately discard segment model objects.
    flattened = [(polygon_points(polygon, opts), polygon.is_island is False)
                 for polygon in geometry.polygons]
    rings: list[Ring] = []
    for points, is_hole in flattened:
        while len(points) > 1 and points[0] == points[-1]:
            points.pop()
        ring = _make_ring(points, is_hole)
        if ring is not None:
            rings.append(ring)
    return Region(rings)


def polygon_points_of(polygon: Polygon) -> list[Point]:
    """Closed ring points of an arc-free polygon."""
    points = [polygon.start]
    points.extend(part.end for part in polygon.parts)
    while len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


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


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

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


def _to_grid(points: Sequence[Point], factor: float) -> list[tuple[int, int]]:
    """Quantise to an integer grid of *factor* steps per millimetre.

    Only **consecutive** duplicates are removed, plus a repeated closing point.
    A non-adjacent repeat is kept: it means the contour touches itself, and
    deleting it reconnects two edges that only meet at a point, which changes
    the filled area.  A two-lobe polygon joined by a neck narrower than the
    grid used to lose the neck *and* gain a square millimetre, because the two
    lobes were welded into one rectangle.  Clipper's nonzero fill resolves such
    self-touching paths into their simple parts, which is what
    :func:`snap_to_grid` emits.
    """
    out: list[tuple[int, int]] = []
    for x, y in points:
        point = (int(round(x * factor)), int(round(y * factor)))
        if not out or out[-1] != point:
            out.append(point)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def snap_to_grid(region: Region, digits: int) -> tuple[Region, int]:
    """Regularise *region* onto the output coordinate grid.

    Rounding each coordinate independently at serialisation time is not safe:
    two nearby vertices can merge, a narrow ring can collapse onto a line, and an
    edge can be pushed across a neighbour so the printed polygon is
    self-intersecting -- while ``area_mm2`` still describes the *pre-rounding*
    shape.  Consumers then receive polygons that are invalid or enclose nothing,
    with no diagnostic.

    This does the quantisation *as geometry*: every vertex is snapped to a
    ``10**-digits`` mm grid, the whole region is re-unioned with the nonzero fill
    rule (which resolves crossings, merges slivers and re-derives island/hole
    nesting), and the result is wound to the spec convention.  Every output
    vertex is an exact multiple of the grid step, so serialisation cannot change
    the shape again.

    Returns ``(region, collapsed)`` where *collapsed* is how many input rings had
    no area left at the target resolution.  Dropping them is correct -- a ring
    with zero area is not a polygon -- but the count is returned so callers can
    report it rather than lose it silently.
    """
    if not region:
        return Region(), 0

    factor = float(10 ** max(0, int(digits)))
    engine = pyclipper.Pyclipper()
    collapsed = 0
    added = 0
    for ring in region.rings:
        path = _to_grid(ring.points, factor)
        if len(path) < 3:
            collapsed += 1
            continue
        try:
            engine.AddPath(path, pyclipper.PT_SUBJECT, True)
            added += 1
        except pyclipper.ClipperException:
            # Clipper rejects a zero-area path outright.  SimplifyPolygon splits
            # a self-touching ring into its simple parts and returns nothing for
            # one that is genuinely flat; the latter is what a collapsed sliver
            # looks like by the time it reaches here.
            parts = [p for p in pyclipper.SimplifyPolygon(path, _FILL)
                     if len(p) >= 3]
            if not parts:
                collapsed += 1
                continue
            for part in parts:
                engine.AddPath(part, pyclipper.PT_SUBJECT, True)
                added += 1

    if not added:
        return Region(), collapsed

    tree = engine.Execute2(pyclipper.CT_UNION, _FILL, _FILL)
    rings: list[Ring] = []

    def emit(contour, is_hole: bool) -> None:
        nonlocal collapsed
        # Clipper's own union can return a *weakly* simple contour -- valid for
        # its nonzero fill, but with a vertex visited twice when two features
        # meet at a point.  A consumer validating rings rejects that, so each
        # contour goes through SimplifyPolygon, which splits it into strictly
        # simple loops.  It is cheap relative to the union that produced them
        # (about a third of it on the worst layer of designodb-rigidflex).
        try:
            parts = pyclipper.SimplifyPolygon([tuple(p) for p in contour], _FILL)
        except pyclipper.ClipperException:
            parts = []
        if not parts:
            collapsed += 1
            return
        for part in parts:
            if len(part) < 3:
                continue
            points = [(x / factor, y / factor) for x, y in part]
            ring = _make_ring(points, is_hole=is_hole)
            if ring is None:
                collapsed += 1
            else:
                rings.append(ring)

    def walk(parent) -> None:
        for child in parent.Childs:
            emit(child.Contour, bool(child.IsHole))
            walk(child)

    walk(tree)
    return Region(rings), collapsed


def simplify(region: Region) -> Region:
    """Resolve self-intersections in a single region.

    Spec p.40 forbids self-intersecting polygons outright, so this is a repair
    for input that violates the spec, not a routine step.  Callers are expected
    to report it.
    """
    if not region:
        return Region()
    engine = pyclipper.Pyclipper()
    for ring in region.rings:
        engine.AddPath(_to_clipper(ring.points), pyclipper.PT_SUBJECT, True)
    tree = engine.Execute2(pyclipper.CT_UNION, _FILL, _FILL)
    return _region_from_tree(tree)


# ---------------------------------------------------------------------------
# Minkowski sweep
# ---------------------------------------------------------------------------

def sweep_polyline(pattern: Iterable[Point], path: Sequence[Point]) -> Region:
    """Sweep *pattern* (a single convex-or-not island ring) along *path*.

    This is the Minkowski sum of the pattern with an **open** polyline, which is
    exactly what spec p.38 describes: *"Lines and arcs are drawn by dragging the
    symbol by its center along a line or circular curve from one end to the
    other."*

    Because the symbol is dragged rather than rotated, its orientation is fixed
    for the whole sweep.  A square symbol therefore produces axis-aligned square
    line ends even on a diagonal line -- which is why the area of a 1 mm square
    swept along a 5 mm 45-degree line is ``1 + 5*sqrt(2)*sqrt(2) = 11`` mm^2, not
    the 8 mm^2 a rotating aperture would give.
    """
    pattern_pts = _to_clipper(list(pattern))
    path_pts = _to_clipper(list(path))
    if len(pattern_pts) < 3 or len(path_pts) < 2:
        return Region()
    # A closed centreline must end where it started, or the Minkowski sweep
    # misses the final segment exactly as the open round stroke used to.
    if _is_closed_centreline(path) and len(path_pts) >= 3:
        path_pts = path_pts + [path_pts[0]]

    # MinkowskiSum wants a positive-orientation pattern for a predictable result.
    from .geometry import signed_area

    if signed_area([(x, y) for x, y in pattern_pts]) < 0:
        pattern_pts.reverse()

    result = pyclipper.MinkowskiSum(pattern_pts, path_pts, False)

    # MinkowskiSum returns plain paths, so nesting has to be resolved.  Running
    # them back through a union gives us the tree (and therefore IsHole) for
    # free, instead of counting containment ourselves.
    engine = pyclipper.Pyclipper()
    for path_out in result:
        if len(path_out) >= 3:
            engine.AddPath(path_out, pyclipper.PT_SUBJECT, True)
    tree = engine.Execute2(pyclipper.CT_UNION, _FILL, _FILL)
    return _region_from_tree(tree)


# ---------------------------------------------------------------------------
# batched sweeps
#
# A production board has hundreds of thousands of line records.  These entry
# points exist so that many centrelines sharing one stroke definition go through
# a single Clipper call, instead of one call per record.
# ---------------------------------------------------------------------------

def _union_paths(paths: Iterable[Sequence[tuple[int, int]]]) -> Region:
    """Union raw Clipper paths, resolving nesting through the poly tree.

    Every path is added as a *subject*.  That works for both conventional
    windings: an island wound one way and its hole the other cancel under
    NONZERO, so no assumption about Clipper's output convention is needed.
    """
    engine = pyclipper.Pyclipper()
    added = False
    for path in paths:
        if len(path) >= 3:
            engine.AddPath(list(path), pyclipper.PT_SUBJECT, True)
            added = True
    if not added:
        return Region()
    return _region_from_tree(engine.Execute2(pyclipper.CT_UNION, _FILL, _FILL))


def _is_closed_centreline(points: Sequence[Point]) -> bool:
    """True when *points* traces a closed loop (its first point is its last).

    A full-circle arc (spec p.40: start == end) is the case this exists for.
    The closing duplicate is compared on the quantised grid, so two endpoints
    that differ by less than a nanometre still count as the same vertex.
    """
    if len(points) < 4:
        return False
    first = (int(round(points[0][0] * SCALE)), int(round(points[0][1] * SCALE)))
    last = (int(round(points[-1][0] * SCALE)), int(round(points[-1][1] * SCALE)))
    return first == last


def stroke_paths(paths: Iterable[Sequence[Point]], radius_mm: float,
                 options=None) -> Region:
    """Round sweep of many centrelines in one pass.

    This is the exact construction for a round symbol: offsetting an open
    centreline by the symbol radius gives round caps and round joins, and the
    band width is exactly ``2 * radius_mm`` regardless of tessellation.  That
    matters -- tessellating the circle first and Minkowski-summing loses width
    (an inscribed 10-gon is only 95% of the true diameter), which on a long thin
    trace is a 5% width error rather than a 5% area error.

    A **closed** centreline (a full circle) is offset as a closed line, not as
    an open one.  Treating it as open drops the segment from its last sample
    back to its first: the result is an open arc band whose round caps hide the
    gap when the stroke is wide and leave no hole at all when it is narrow.
    ``ET_CLOSEDLINE`` joins the ends instead of capping them, so the band is a
    genuine annulus.

    ``ArcTolerance`` is the sagitta of the generated round caps, so the same
    tessellation budget that governs arcs governs cap smoothness.
    """
    if radius_mm <= 0.0:
        return Region()
    opts = options or TessellationOptions()

    offsetter = pyclipper.PyclipperOffset()
    added = False
    for points in paths:
        closed = _is_closed_centreline(points)
        quantised = _to_clipper(points)
        if len(quantised) < (3 if closed else 2):
            continue
        end_type = pyclipper.ET_CLOSEDLINE if closed else pyclipper.ET_OPENROUND
        offsetter.AddPath(quantised, pyclipper.JT_ROUND, end_type)
        added = True
    if not added:
        return Region()

    offsetter.ArcTolerance = max(1.0, opts.sagitta_mm * SCALE)
    return _union_paths(offsetter.Execute(int(round(radius_mm * SCALE))))


def stroke_path(paths: Iterable[Sequence[Point]], radius_mm: float,
                options=None) -> Region:
    """Single-stroke convenience wrapper around :func:`stroke_paths`."""
    return stroke_paths(paths, radius_mm, options)


def sweep_paths(patterns: Iterable[Sequence[Point]],
                paths: Iterable[Sequence[Point]],
                options=None) -> Region:
    """Minkowski sweep of each *pattern* along every *path*.

    Exact for a single convex pattern, which covers every symmetric standard
    symbol the spec sanctions for lines and arcs.  Unlike :func:`stroke_paths`
    this has no closed form for a circle, so a round pattern tessellated before
    sweeping inherits the tessellation error -- use :func:`stroke_paths` for
    round symbols.
    """
    from .geometry import signed_area

    clipper_paths: list[list[tuple[int, int]]] = []
    for points in paths:
        path = _to_clipper(points)
        if len(path) < 2:
            continue
        # Close the loop so the sweep covers the last segment too.
        if _is_closed_centreline(points) and len(path) >= 3:
            path = path + [path[0]]
        clipper_paths.append(path)
    if not clipper_paths:
        return Region()

    out = Region()
    for pattern in patterns:
        quantised = _to_clipper(pattern)
        if len(quantised) < 3:
            continue
        if signed_area([(float(x), float(y)) for x, y in quantised]) < 0:
            quantised.reverse()
        out = union(out, _union_paths(
            pyclipper.MinkowskiSum2(quantised, clipper_paths, False)
        ))
    return out


def offset(region: Region, delta_mm: float, options=None) -> Region:
    """Grow (*delta_mm* > 0) or shrink (*delta_mm* < 0) a solid region.

    Used for the ``apt_def`` resize factor.  Corners are rounded, so a resized
    rectangle gains round corners; the spec does not say what should happen
    there and this is the conventional reading.
    """
    if not region or delta_mm == 0.0:
        return Region()
    opts = options or TessellationOptions()

    offsetter = pyclipper.PyclipperOffset()
    added = False
    for ring in region.rings:
        quantised = _to_clipper(ring.points)
        if len(quantised) < 3:
            continue
        offsetter.AddPath(quantised, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        added = True
    if not added:
        return Region()

    offsetter.ArcTolerance = max(1.0, opts.sagitta_mm * SCALE)
    return _union_paths(offsetter.Execute(int(round(delta_mm * SCALE))))


def sweep(geometry: SurfaceGeometry, path: Sequence[Point],
          options=None) -> Region:
    """Sweep a whole symbol (islands minus holes) along *path*.

    A symbol with holes -- a donut, say -- sweeps its holes too, producing a
    tunnel through the swept band.  Spec p.38 only sanctions round and square
    symbols for lines ("using non-symmetric symbols for the drawing of lines is
    not recommended"), so this general path exists because the spec also says an
    application that meets such a line must still do something reasonable: *"The
    result should resemble the sweep of the symbol from its start point to its
    end point."*  The obvious generalisation -- sweep the islands, subtract the
    swept holes -- is what is implemented here, and callers are expected to warn.
    """
    from .geometry import geometry_points

    flattened = geometry_points(geometry, options)
    if not flattened or len(path) < 2:
        return Region()

    swept = Region()
    for polygon in flattened:
        if polygon.is_island:
            swept = union(swept, sweep_polyline(polygon_points_of(polygon), path))
    for polygon in flattened:
        if not polygon.is_island:
            hole_sweep = sweep_polyline(polygon_points_of(polygon), path)
            if hole_sweep:
                swept = difference(swept, hole_sweep)
    return swept
