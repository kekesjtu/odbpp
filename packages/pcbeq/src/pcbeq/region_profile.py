"""Physical profile coordinates and exact common strip intervals."""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from .geometry import Region, Ring


def perimeter(region: Region) -> float:
    """Total boundary length of *region* in millimetres."""
    total = 0.0
    for ring in region.rings:
        points = ring.points
        count = len(points)
        for index in range(count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % count]
            total += math.hypot(x2 - x1, y2 - y1)
    return total


def profile_shape(region):
    """Simple profile topology only; never used on complex material patterns."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    # Parser polygons are already positive. Keep their actual coordinates and
    # derived step/layer intersections: re-unioning through the integer kernel
    # would quantize these simple physical domains a second time.
    islands = [Polygon(r.points) for r in region.islands]
    holes = [[] for _ in islands]
    for ring in region.holes:
        hole = Polygon(ring.points)
        parents = [i for i, shape in enumerate(islands) if shape.covers(hole)]
        if not parents:
            raise ValueError(f"profile hole has no containing island: {ring.bounds}")
        holes[min(parents, key=lambda i: islands[i].area)].append(ring.points)
    shape = unary_union([Polygon(r.points, h) for r, h in zip(region.islands, holes)])
    if not shape.is_valid:
        raise ValueError("invalid profile topology; regenerate parser JSON")
    return shape


def _components(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == "Polygon":
        return [shape]
    return [p for g in getattr(shape, "geoms", ()) for p in _components(g)]


def profile_region(shape):
    """Keep derived simple-domain coordinates outside the integer material kernel."""
    components=_components(shape)
    return Region([Ring(tuple(p.exterior.coords)[:-1],False) for p in components]+
                  [Ring(tuple(h.coords)[:-1],True) for p in components for h in p.interiors])


def _intersect_spans(a, b):
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo + 1e-9: out.append((lo, hi))
        if a[i][1] < b[j][1]: i += 1
        else: j += 1
    return out


def _crossings(rings, x):
    edges = [(x0,y0,x1,y1) for ring in rings
             for (x0,y0),(x1,y1) in zip(ring.points,ring.points[1:]+ring.points[:1])]
    return _edge_crossings(edges,x)


def _edge_crossings(edges, x):
    ys = [y0+(y1-y0)*(x-x0)/(x1-x0) for x0,y0,x1,y1 in edges
          if (x0 <= x) != (x1 <= x)]
    ys.sort()
    return list(zip(ys[::2], ys[1::2]))


class _ProfileIndex:
    """Immutable physical profile events; independent of any raster."""

    def __init__(self, region):
        self.vertices = sorted({x for ring in region.rings for x,y in ring.points})
        self.edges = sorted(
            [(min(x0,x1),max(x0,x1),(x0,y0,x1,y1)) for ring in region.rings
             for (x0,y0),(x1,y1) in zip(ring.points,ring.points[1:]+ring.points[:1])
             if x0 != x1], key=lambda edge:edge[0])
        self.starts = [edge[0] for edge in self.edges]

    def slab(self, a, b):
        events = [a]+self.vertices[bisect_right(self.vertices,a):bisect_left(self.vertices,b)]+[b]
        # Keep endpoint edges so exact one-sided limits can be evaluated.
        edges = [edge for low,high,edge in self.edges[:bisect_right(self.starts,b)] if high >= a]
        return events,edges


def _envelope(region, a, b, index=None):
    """True common Y intervals over a complete X strip, without Y quantization.

    Between consecutive vertex events the active edges are affine. Evaluate
    their exact one-sided endpoint limits; an inward probe would miss an edge
    extremum, and rounding the resulting Y values can expand the rectangle.
    """
    events,edges = (index or _ProfileIndex(region)).slab(a,b)
    spans = None
    for left,right in zip(events, events[1:]):
        active = [edge for edge in edges
                  if min(edge[0],edge[2]) <= left and max(edge[0],edge[2]) >= right]
        endpoints = []
        for x in (left,right):
            ys = [y0 if x == x0 else y1 if x == x1
                  else y0+(y1-y0)*(x-x0)/(x1-x0) for x0,y0,x1,y1 in active]
            ys.sort()
            endpoints.append(ys)
        # Match interval identities BEFORE intersecting event ranges. Merely
        # intersecting endpoint unions invents material when a moving hole gap
        # collapses at a vertex: a Y value can be solid at both ends yet cross
        # that gap in the interior.
        value = [(max(a0,b0),min(a1,b1))
                 for a0,a1,b0,b1 in zip(endpoints[0][::2],endpoints[0][1::2],
                                         endpoints[1][::2],endpoints[1][1::2])
                 if min(a1,b1) > max(a0,b0)+1e-8]
        spans = value if spans is None else _intersect_spans(spans,value)
    return [(low,high) for low,high in spans or [] if high > low+1e-8]
