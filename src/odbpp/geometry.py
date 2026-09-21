"""Geometry: native expansion of standard symbols and arc discretisation.

Two jobs live here.

**Arc discretisation.**  ODB++ keeps arcs as ``OC <xe> <ye> <xc> <yc> cw`` and
expects a polygon boundary to be a closed ring of *segments* (spec p.39 puts no
limit on the number of segments but forbids self-intersection).  Flattening an
arc therefore needs a tolerance, not a fixed segment count: a 25 mm milled arc
and a 0.05 mm pad corner want very different tessellation.  Everything here
takes an explicit ``sagitta_mm`` -- the maximum allowed distance between the true
curve and its chord.

**Native symbol expansion.**  AGENTS constraint 5 requires ``r``/``s``/``rect``/
``oval`` (and their close relatives) to be expanded from their *name* rather
than looked up in ``symbols/``.  :func:`standard_symbol_geometry` turns a parsed
:class:`~odbpp.symbols.StandardSymbol` into a :class:`~odbpp.model.SurfaceGeometry`
in millimetres, centred on the origin, with the spec's orientation convention
(islands clockwise, holes counter-clockwise -- spec p.39).

Symbol parameters use the *size* unit semantic (micrometre/mil), never the
coordinate one, so :meth:`StandardSymbol.size_mm` is the only converter used
here.  Getting that wrong is a silent 1000x error.

Every ring produced below is built **clockwise from the start**, i.e. it is
already in the spec's island orientation; :func:`ensure_winding` is then a no-op
check rather than a repair.

Not implemented yet: thermals (``thr``/``ths``/``*_ths``/``*_tho``), ``bfr``/
``bfs``, ``moire``, ``ext``, ``el`` and the stencil families (``*plate``,
``dshape``, ``cross``, ``dogbone``, ``dpack``).  Those raise
:class:`~odbpp.errors.UnsupportedFeatureError` rather than returning a plausible
but wrong shape.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .errors import UnsupportedFeatureError
from .model import ContourPart, Point, Polygon, SurfaceGeometry
from .symbols import StandardSymbol

__all__ = [
    "DEFAULT_SAGITTA_MM",
    "TessellationOptions",
    "arc_points",
    "arc_vertices",
    "contour_points",
    "polygon_points",
    "geometry_points",
    "signed_area",
    "is_clockwise",
    "ensure_winding",
    "bounding_box",
    "transform_points",
    "rotate_points",
    "mirror_x",
    "standard_symbol_geometry",
    "SUPPORTED_SYMBOL_PREFIXES",
]

#: 5 micrometres: comfortably below any PCB fabrication tolerance, and coarse
#: enough that a 1 mm arc becomes ~4 segments rather than hundreds.
DEFAULT_SAGITTA_MM = 0.005

_TWO_PI = 2.0 * math.pi
_HALF_PI = 0.5 * math.pi


@dataclass(frozen=True)
class TessellationOptions:
    """How faithfully to flatten curves."""

    #: Maximum chord-to-arc distance, in millimetres.
    sagitta_mm: float = DEFAULT_SAGITTA_MM
    #: Clamp on segments per arc, so a degenerate radius cannot explode.
    max_segments: int = 4096

    def __post_init__(self) -> None:
        if self.sagitta_mm <= 0.0:
            raise ValueError("sagitta_mm must be positive")
        if self.max_segments < 1:
            raise ValueError("max_segments must be at least 1")


# ---------------------------------------------------------------------------
# arcs
# ---------------------------------------------------------------------------

def _segment_count(sweep: float, radius: float, options: TessellationOptions) -> int:
    """Number of chords needed to keep the sagitta under the tolerance."""
    if radius <= 0.0 or options.sagitta_mm >= radius:
        # A tolerance wider than the radius cannot resolve the curve at all;
        # quarter-turns are the most that can be justified.
        return max(1, math.ceil(abs(sweep) / _HALF_PI))
    # chord half-angle: cos(theta/2) = 1 - sagitta / radius
    half_angle = math.acos(max(-1.0, min(1.0, 1.0 - options.sagitta_mm / radius)))
    if half_angle <= 0.0:
        return options.max_segments
    return max(1, min(math.ceil(abs(sweep) / (2.0 * half_angle)), options.max_segments))


def arc_vertices(
    center: Point,
    radius: float,
    start_angle: float,
    sweep: float,
    options: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Points along an arc given in polar form, **including both endpoints**."""
    opts = options or TessellationOptions()
    if radius <= 0.0:
        return []
    count = _segment_count(sweep, radius, opts)
    return [(center[0] + radius * math.cos(start_angle + sweep * i / count),
             center[1] + radius * math.sin(start_angle + sweep * i / count))
            for i in range(count + 1)]


def arc_points(
    start: Point,
    end: Point,
    center: Point,
    clockwise: bool,
    options: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Discretise the arc from *start* to *end* around *center*.

    Returns the **intermediate** points plus the final point, i.e. *start* is
    excluded because the caller's contour already holds it.  When start and end
    coincide the spec calls it a full 360 degree turn (p.40), so a whole circle
    is produced.

    The last returned point is *end* verbatim rather than the recomputed point on
    the circle, which keeps a polygon's recorded closure exact even when the
    authored start/end radii differ by a rounding amount.
    """
    opts = options or TessellationOptions()

    dx1, dy1 = start[0] - center[0], start[1] - center[1]
    dx2, dy2 = end[0] - center[0], end[1] - center[1]
    r1 = math.hypot(dx1, dy1)
    r2 = math.hypot(dx2, dy2)

    if r1 <= 0.0 or r2 <= 0.0:
        # The centre coincides with an endpoint, so no circle through both points
        # exists.  Spec p.39 requires a legal curve; generate the chord instead of
        # a radius-zero sweep that would put every vertex on the centre.
        return [end]

    radius = 0.5 * (r1 + r2)

    a1 = math.atan2(dy1, dx1)
    a2 = math.atan2(dy2, dx2)

    if math.hypot(dx2 - dx1, dy2 - dy1) <= max(1e-12, radius * 1e-9):
        # Start and end coincide: a full turn (spec p.40).
        sweep = -_TWO_PI if clockwise else _TWO_PI
        count = _segment_count(sweep, radius, opts)
        return [(center[0] + radius * math.cos(a1 + sweep * i / count),
                 center[1] + radius * math.sin(a1 + sweep * i / count))
                for i in range(1, count)] + [end]

    if clockwise:
        sweep = -((a1 - a2) % _TWO_PI) or -_TWO_PI
    else:
        sweep = (a2 - a1) % _TWO_PI or _TWO_PI

    count = _segment_count(sweep, radius, opts)
    points = [(center[0] + radius * math.cos(a1 + sweep * i / count),
               center[1] + radius * math.sin(a1 + sweep * i / count))
              for i in range(1, count)]
    points.append(end)
    return points


# ---------------------------------------------------------------------------
# contours
# ---------------------------------------------------------------------------

def contour_points(
    start: Point,
    parts: Iterable[ContourPart],
    options: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Flatten a contour into a closed ring of points.

    The first point is *start* and the last point repeats it when the source was
    well formed (spec p.39 requires the last record point to equal the first).
    The duplication is preserved rather than dropped, so callers can detect a
    contour that was never closed instead of having that repaired silently.
    """
    opts = options or TessellationOptions()
    points: list[Point] = [start]
    current = start

    for part in parts:
        if part.is_arc:
            if part.center is None:
                raise UnsupportedFeatureError("arc without a centre point")
            points.extend(arc_points(current, part.end, part.center,
                                     bool(part.clockwise), opts))
        else:
            points.append(part.end)
        current = part.end

    return points


def polygon_points(
    polygon: Polygon,
    options: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Flatten one :class:`~odbpp.model.Polygon`."""
    return contour_points(polygon.start, polygon.parts, options)


def geometry_points(
    geometry: SurfaceGeometry,
    options: Optional[TessellationOptions] = None,
) -> list[Polygon]:
    """Return a copy of *geometry* with every arc replaced by segments.

    Arc information is discarded deliberately: callers that want to keep curves
    should tessellate at the last possible moment.
    """
    opts = options or TessellationOptions()
    out: list[Polygon] = []
    for polygon in geometry.polygons:
        pts = polygon_points(polygon, opts)
        out.append(Polygon(
            is_island=polygon.is_island,
            start=pts[0],
            parts=[ContourPart(kind="segment", end=p) for p in pts[1:]],
        ))
    return out


# ---------------------------------------------------------------------------
# ring arithmetic
# ---------------------------------------------------------------------------

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


def ensure_winding(points: list[Point], clockwise: bool) -> list[Point]:
    """Reverse *points* if their winding does not match *clockwise*.

    Spec p.39: island boundaries run clockwise, hole boundaries counter-clockwise.
    """
    if len(points) < 3:
        return points
    if is_clockwise(points) == clockwise:
        return points
    return list(reversed(points))


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


# ---------------------------------------------------------------------------
# placement transforms
# ---------------------------------------------------------------------------

def rotate_points(points: Sequence[Point], degrees: float) -> list[Point]:
    """Rotate **clockwise** by *degrees* (spec p.34: ODB++ rotation is clockwise)."""
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return [(x * cos_t + y * sin_t, -x * sin_t + y * cos_t) for x, y in points]


def mirror_x(points: Sequence[Point]) -> list[Point]:
    """Mirror along the x-axis, i.e. negate y (spec p.34: only x-mirroring exists)."""
    return [(x, -y) for x, y in points]


def transform_points(
    points: Sequence[Point],
    *,
    angle: float = 0.0,
    mirrored: bool = False,
) -> list[Point]:
    """Apply an ODB++ placement: rotate first, then mirror (spec p.34).

    The order matters and is specified: *"When pads are both rotated and
    mirrored, they are rotated first, and then mirrored along the x-axis."*
    """
    out = rotate_points(points, angle) if angle else list(points)
    return mirror_x(out) if mirrored else out


# ---------------------------------------------------------------------------
# basic shapes (all built clockwise from the start point)
# ---------------------------------------------------------------------------

def _circle(diameter: float, opts: TessellationOptions) -> list[Point]:
    """Circle centred on the origin, clockwise from angle 0."""
    radius = diameter * 0.5
    if radius <= 0.0:
        return []
    count = _segment_count(_TWO_PI, radius, opts)
    return [(radius * math.cos(-_TWO_PI * i / count),
             radius * math.sin(-_TWO_PI * i / count)) for i in range(count)]


def _square(side: float) -> list[Point]:
    if side <= 0.0:
        return []
    h = side * 0.5
    return [(h, h), (h, -h), (-h, -h), (-h, h)]


#: Corner sites, walked clockwise starting from the top-right (spec p.203).
#: ``(x, y, arc_enter_angle)`` where the angle is the polar direction, seen from
#: the corner's arc centre, at which the boundary arrives.
_CORNER_SITES: dict[int, tuple[float, float, float]] = {
    1: (1.0, 1.0, _HALF_PI),      # top-right:    arrive along the top edge
    4: (1.0, -1.0, 0.0),          # bottom-right: arrive down the right edge
    3: (-1.0, -1.0, -_HALF_PI),   # bottom-left:  arrive along the bottom edge
    2: (-1.0, 1.0, math.pi),      # top-left:     arrive up the left edge
}

#: Clockwise visit order for the four corners.
_CORNER_ORDER = (1, 4, 3, 2)


def _rect(
    width: float,
    height: float,
    *,
    radius: float = 0.0,
    chamfer: float = 0.0,
    corners: Sequence[int] = (),
    opts: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Rectangle with optional rounded or chamfered corners.

    Corner numbering follows spec p.203: ascending counter-clockwise from the
    **top-right** (1 = top-right, 2 = top-left, 3 = bottom-left, 4 =
    bottom-right).  An empty *corners* means all four.

    **Unverified:** the spec's prose and its own example disagree about the
    chamfer corner list -- ``rect100x50xc8x12`` is described as rounding "only
    the top right corner" while ``12`` names two corners.  This implementation
    follows the field description (each digit names one corner) and never
    silently rounds more corners than were asked for.
    """
    half_w, half_h = width * 0.5, height * 0.5
    if half_w <= 0.0 or half_h <= 0.0:
        return []

    selected = set(corners) if corners else {1, 2, 3, 4}
    unknown = selected - {1, 2, 3, 4}
    if unknown:
        raise UnsupportedFeatureError(
            f"corner mask names corners outside 1..4: {sorted(unknown)}"
        )

    points: list[Point] = []
    for corner in _CORNER_ORDER:
        sign_x, sign_y, enter = _CORNER_SITES[corner]
        cx, cy = sign_x * half_w, sign_y * half_h

        if corner not in selected:
            points.append((cx, cy))
            continue

        if radius > 0.0:
            cut = min(radius, min(half_w, half_h))
            arc_center = (cx - sign_x * cut, cy - sign_y * cut)
            points.extend(arc_vertices(arc_center, cut, enter, -_HALF_PI, opts))
        elif chamfer > 0.0:
            cut = min(chamfer, min(half_w, half_h))
            if corner in (1, 3):
                points.append((cx - sign_x * cut, cy))
                points.append((cx, cy - sign_y * cut))
            else:
                points.append((cx, cy - sign_y * cut))
                points.append((cx - sign_x * cut, cy))
        else:
            points.append((cx, cy))

    return _dedupe_ring(points)


def _oval(
    width: float,
    height: float,
    *,
    half: bool = False,
    opts: Optional[TessellationOptions] = None,
) -> list[Point]:
    """Obround: a rectangle capped by semicircles along its major axis.

    The straight sides run parallel to the **longer** axis (spec p.188 names
    only ``w`` and ``h``), so a wide oval has its caps left and right.
    ``oval_h`` (half oval) is the upper half, closed along the diameter.
    """
    options = opts or TessellationOptions()
    if width <= 0.0 or height <= 0.0:
        return []

    if half:
        return _half_oval(width, height, options)

    if width >= height:
        r = height * 0.5
        s = (width - height) * 0.5
        points = arc_vertices((s, 0.0), r, _HALF_PI, -math.pi, options)   # right cap
        points.append((-s, -r))
        points.extend(arc_vertices((-s, 0.0), r, -_HALF_PI, -math.pi, options))  # left cap
        points.append((s, r))
    else:
        r = width * 0.5
        s = (height - width) * 0.5
        points = [(r, -s), (r, s)]
        points.extend(arc_vertices((0.0, s), r, 0.0, math.pi, options))   # top cap
        points.append((-r, -s))
        points.extend(arc_vertices((0.0, -s), r, math.pi, math.pi, options))  # bottom cap

    return _dedupe_ring(points)


def _half_oval(width: float, height: float,
               opts: TessellationOptions) -> list[Point]:
    """Upper half of an obround, closed by the diameter along y = 0."""
    if width >= height:
        r = height * 0.5
        s = (width - height) * 0.5
        points = arc_vertices((s, 0.0), r, _HALF_PI, -_HALF_PI, opts)  # (s,r) -> (s,0)
        points.extend(arc_vertices((-s, 0.0), r, math.pi, -_HALF_PI, opts))  # (-s,0) -> (-s,r)
        # Close along the flat top edge back to the start.
        points.append((s, r))
    else:
        r = width * 0.5
        s = (height - width) * 0.5
        points = arc_vertices((0.0, s), r, 0.0, math.pi, opts)  # (r,s) -> (-r,s)
        points.append((-r, 0.0))
        points.append((r, 0.0))
        points.append((r, s))
    return _dedupe_ring(points)


def _triangle(base: float, height: float) -> list[Point]:
    """Isosceles triangle spanning exactly ``base`` x ``height`` (spec p.192).

    Not built from :func:`_regular`: ``tri<base>x<h>`` fixes the *base length and
    the height*, whereas inscribing three vertices in a base x height ellipse
    gives a triangle only 3h/4 tall.
    """
    if base <= 0.0 or height <= 0.0:
        return []
    half_b, half_h = base * 0.5, height * 0.5
    return [(0.0, half_h), (half_b, -half_h), (-half_b, -half_h)]


def _hexagon(width: float, height: float, *, vertical: bool) -> list[Point]:
    """Hexagon spanning exactly ``width`` x ``height`` (spec p.191).

    Built directly rather than inscribed in the ellipse: the spec gives ``w`` and
    ``h`` as the hexagon's overall width and height, and an ellipse-inscribed
    hexagon falls short of the box on the flat edges.

    ``vertical=False`` (``hex_l``) puts vertices left and right with a flat top;
    ``vertical=True`` (``hex_s``) puts them at the top and bottom.
    """
    if width <= 0.0 or height <= 0.0:
        return []
    rx, ry = width * 0.5, height * 0.5
    if vertical:
        return [(0.0, ry), (rx, ry * 0.5), (rx, -ry * 0.5),
                (0.0, -ry), (-rx, -ry * 0.5), (-rx, ry * 0.5)]
    return [(rx, 0.0), (rx * 0.5, ry), (-rx * 0.5, ry),
            (-rx, 0.0), (-rx * 0.5, -ry), (rx * 0.5, -ry)]


def _octagon(width: float, height: float, corner: float) -> list[Point]:
    """Octagon spanning exactly ``width`` x ``height``.

    ``corner`` is the distance between the bounding-box corner and the vertex
    (spec p.204).  When it is omitted the largest octagon that still touches all
    four sides is used: the affine image of a regular octagon, whose corner cut
    is ``2 - sqrt(2)`` of each half axis (so a unit box yields a true regular
    octagon with side ``1 / (1 + sqrt(2))``).
    """
    if width <= 0.0 or height <= 0.0:
        return []
    rx, ry = width * 0.5, height * 0.5
    if corner > 0.0:
        cx = min(corner, rx)
        cy = min(corner, ry)
    else:
        k = 2.0 - math.sqrt(2.0)
        cx, cy = rx * k, ry * k
    return [(rx, ry - cy), (rx - cx, ry),
            (-rx + cx, ry), (-rx, ry - cy),
            (-rx, -ry + cy), (-rx + cx, -ry),
            (rx - cx, -ry), (rx, -ry + cy)]


def _regular(sides: int, width: float, height: float, *,
             rotation: float = 0.0, corner: float = 0.0) -> list[Point]:
    """Regular polygon inscribed in the w x h box, clockwise from the top.

    ``rotation`` is measured from the top (12 o'clock) and turns clockwise with
    the vertex order.  ``corner`` is the octagon's corner size: the distance
    between the bounding-box corner and the vertex (spec p.204), which is what
    turns the square into an octagon.
    """
    if width <= 0.0 or height <= 0.0:
        return []
    if sides == 8:
        return _octagon(width, height, corner)

    rx, ry = width * 0.5, height * 0.5
    # Clockwise means decreasing angle.
    start = _HALF_PI + rotation
    return [(rx * math.cos(start - _TWO_PI * i / sides),
             ry * math.sin(start - _TWO_PI * i / sides)) for i in range(sides)]


def _dedupe_ring(points: Sequence[Point], epsilon: float = 1e-12) -> list[Point]:
    """Drop consecutive duplicates and a duplicated closing point."""
    out: list[Point] = []
    for point in points:
        if out and math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) <= epsilon:
            continue
        out.append((point[0], point[1]))
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0],
                                      out[0][1] - out[-1][1]) <= epsilon:
        out.pop()
    return out


def _scaled(points: Sequence[Point], factor: float) -> list[Point]:
    return [(x * factor, y * factor) for x, y in points]


#: Symbol prefixes this module can expand natively.
SUPPORTED_SYMBOL_PREFIXES: frozenset[str] = frozenset({
    "r", "s", "rect", "oval", "oval_h", "di", "oct", "tri", "hex_l", "hex_s",
    "donut_r", "donut_s", "donut_rc", "donut_o",
})


def standard_symbol_geometry(
    symbol: StandardSymbol,
    options: Optional[TessellationOptions] = None,
) -> SurfaceGeometry:
    """Expand a standard symbol into millimetre polygons centred on the origin.

    Raises :class:`~odbpp.errors.UnsupportedFeatureError` for a family that is
    not implemented, so a caller never receives a stand-in shape that looks right
    and is not.
    """
    opts = options or TessellationOptions()
    prefix = symbol.prefix
    if prefix not in SUPPORTED_SYMBOL_PREFIXES:
        raise UnsupportedFeatureError(
            f"standard symbol family {prefix!r} ({symbol.name!r}) "
            f"is not expanded natively yet"
        )

    p = symbol.params
    geometry = SurfaceGeometry()

    def size(name: str) -> float:
        value = p.get(name)
        return 0.0 if value is None else symbol.size_mm(value)

    def add(points: Sequence[Point], *, island: bool) -> None:
        ring = _dedupe_ring(points)
        if len(ring) < 3:
            return
        ring = ensure_winding(ring, island)
        geometry.polygons.append(Polygon(
            is_island=island,
            start=ring[0],
            parts=[ContourPart(kind="segment", end=pt) for pt in ring[1:]],
        ))

    if prefix == "r":
        add(_circle(size("d"), opts), island=True)

    elif prefix == "s":
        add(_square(size("s")), island=True)

    elif prefix == "rect":
        add(_rect(size("w"), size("h"),
                  radius=symbol.radius_mm or 0.0,
                  chamfer=symbol.chamfer_mm or 0.0,
                  corners=symbol.corners, opts=opts), island=True)

    elif prefix == "oval":
        add(_oval(size("w"), size("h"), opts=opts), island=True)

    elif prefix == "oval_h":
        add(_oval(size("w"), size("h"), half=True, opts=opts), island=True)

    elif prefix == "di":
        add(_regular(4, size("w"), size("h")), island=True)

    elif prefix == "tri":
        add(_triangle(size("base"), size("h")), island=True)

    elif prefix == "oct":
        add(_octagon(size("w"), size("h"), size("r")), island=True)

    elif prefix == "hex_l":
        # Horizontal hexagon: vertices on the left and right, flat top/bottom.
        add(_hexagon(size("w"), size("h"), vertical=False), island=True)

    elif prefix == "hex_s":
        # Vertical hexagon: vertices at the top and bottom, flat sides.
        add(_hexagon(size("w"), size("h"), vertical=True), island=True)

    elif prefix == "donut_r":
        outer_d, inner_d = size("od"), size("id")
        add(_circle(outer_d, opts), island=True)
        if inner_d > 0.0 and outer_d > 0.0:
            add(_scaled(_circle(outer_d, opts), inner_d / outer_d), island=False)

    elif prefix == "donut_s":
        outer_s, inner_s = size("od"), size("id")
        add(_square(outer_s), island=True)
        add(_square(inner_s), island=False)

    elif prefix == "donut_rc":
        line_width = size("lw")
        add(_rect(size("ow"), size("oh"),
                  radius=symbol.radius_mm or 0.0,
                  chamfer=symbol.chamfer_mm or 0.0,
                  corners=symbol.corners, opts=opts), island=True)
        add(_rect(size("ow") - 2.0 * line_width,
                  size("oh") - 2.0 * line_width), island=False)

    elif prefix == "donut_o":
        line_width = size("lw")
        add(_oval(size("ow"), size("oh"), opts=opts), island=True)
        add(_oval(size("ow") - 2.0 * line_width,
                  size("oh") - 2.0 * line_width, opts=opts), island=False)

    return geometry
