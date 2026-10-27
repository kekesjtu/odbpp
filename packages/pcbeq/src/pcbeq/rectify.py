"""Inscribed staircase planning in physical coordinates, independent of raster."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from .geometry import Region, Ring, union
from .diagnostics import RECTIFY_AREA_ERROR, RECTIFY_EMPTY_PROFILE, Diagnostics

__all__ = ["Rect", "RectifyOptions", "RectifyResult", "rectify_region",
           "DEFAULT_TOLERANCE_MM", "DEFAULT_MAX_BOUNDARY_RETREAT_MM",
           "boundary_retreat", "perimeter"]

#: Initial physical boundary step; adaptive refinement never reads pixels.
DEFAULT_TOLERANCE_MM = 0.5
DEFAULT_MAX_BOUNDARY_RETREAT_MM = 3.0
MAX_INITIAL_BANDS = 10000
# Keep the established topology-safe seed when users relax the error budget;
# coarsening can still consume the larger distance/area budgets afterwards.
MAX_INITIAL_STEP_MM = 0.25


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
    """Two public error budgets, plus internal physical planning limits."""

    max_boundary_retreat_mm: float = DEFAULT_MAX_BOUNDARY_RETREAT_MM
    tolerance_mm: Optional[float] = None
    #: Reject the result when lost or gained area exceeds this fraction of the
    #: original outline area.
    max_relative_area_error: float = 0.05
    min_step_mm: float = 1e-5
    max_refinement_rounds: int = 20
    max_refinement_steps: int = 10000
    basis_mm: Optional[tuple[float, float, float, float]] = None
    #: Internal retry cap for coarsening when independently valid layer models
    #: become invalid after shared-domain intersection. Public budgets stay fixed.
    coarsening_retreat_mm: Optional[float] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_boundary_retreat_mm) or self.max_boundary_retreat_mm <= 0:
            raise ValueError("max_boundary_retreat_mm must be finite and positive")
        if self.coarsening_retreat_mm is not None and (
                not math.isfinite(self.coarsening_retreat_mm) or self.coarsening_retreat_mm <= 0):
            raise ValueError("internal coarsening retreat cap must be finite and positive")
        if self.tolerance_mm is not None and (not math.isfinite(self.tolerance_mm) or self.tolerance_mm <= 0.0):
            raise ValueError(f"tolerance_mm must be positive, got {self.tolerance_mm!r}")
        if not math.isfinite(self.max_relative_area_error) or not 0 <= self.max_relative_area_error < 1:
            raise ValueError("max_relative_area_error must be finite and in [0, 1)")
        if not math.isfinite(self.min_step_mm) or self.min_step_mm <= 0:
            raise ValueError("min_step_mm must be finite and positive")
        for name in ("max_refinement_rounds", "max_refinement_steps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


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

    audit: dict = field(default_factory=dict)

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
            "strategy": "inscribed-vertical-staircase-worst-first",
            "audit": self.audit,
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
    region = union(region)
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


def _topology_source(original):
    from shapely.geometry import Polygon
    sources = _components(original)
    holes = [Polygon(h) for s in sources for h in s.interiors]
    cores = [(radius, _components(original.buffer(-radius)))
             for radius in (0.03125, 0.1, 0.25, 0.5, 1.0)]
    return sources, holes, cores


def _topology(original, model, source=None):
    from shapely.geometry import Polygon
    sources, holes, eroded = source if source is not None else _topology_source(original)
    targets = _components(model)
    points = [t.representative_point() for t in targets]
    matches = [sum(s.covers(point) for point in points) for s in sources]
    model_holes = [Polygon(h) for t in targets for h in t.interiors]
    hole_matches = [sum(m.covers(h) for m in model_holes) for h in holes]
    positive = all(len(_components(t.buffer(-1e-7))) == 1 for t in targets)
    # Erosion splits lobes separated by a narrow neck. Preserve every such
    # geometric core, including terminal branches within a single source island.
    # These scales are physical millimetres, entirely independent of sampling.
    core_checks = []
    for radius, cores in eroded:
        overlaps = [c.intersection(model) for c in cores]
        kept = [not overlap.is_empty and overlap.area > 1e-10 for overlap in overlaps]
        core_checks.append({"erosion_mm":radius,"cores":len(cores),"retained":sum(kept)})
    core_preserved = all(c["cores"] == c["retained"] for c in core_checks)
    return {"original_components": len(sources), "model_components": len(targets),
            "component_correspondence": matches,
            "original_holes": len(holes), "model_holes": len(model_holes),
            "hole_correspondence": hole_matches, "positive_width_connections": positive,
            "protected_core_checks":core_checks,
            "preserved": all(n == 1 for n in matches + hole_matches)
                         and len(targets) == len(sources) and len(model_holes) == len(holes)
                         and positive and core_preserved}


@lru_cache(maxsize=16)
def _distance_tube(line, limit):
    # Shapely geometries are immutable: keys include the full contour and radius.
    return line.buffer(limit, quad_segs=32)


def _line_within(a, b, limit):
    """Continuous, conservative two-sided distance test, not vertex sampling.

    Round polygonal buffers are INSCRIBED in the mathematical distance tube.
    Coverage therefore certifies an upper bound, rather than accepting a sparse
    Hausdorff estimate. Both line directions are tested, including segment interiors.
    """
    if a.equals(b):
        return True
    if a.is_empty or b.is_empty or a.hausdorff_distance(b) > limit:
        return False
    return _distance_tube(b,limit).covers(a) and _distance_tube(a,limit).covers(b)


def _line_bounds(a, b, limit):
    if a.equals(b):
        return 0.0, 0.0
    if a.is_empty or b.is_empty:
        return math.inf, math.inf
    low = a.hausdorff_distance(b)  # GEOS vertex estimate is only a LOWER bound.
    vertex_lower = low
    high = limit if _line_within(a,b,limit) else max(limit*2, low * 1.01)
    for _ in range(64):
        if _line_within(a, b, high):
            break
        high *= 2
    else:
        raise ValueError("cannot certify contour distance within numeric range")
    # Report a certified upper bound, with at most 0.1% of the requested
    # tolerance as measurement uncertainty (apart from buffer conservatism).
    accuracy = max(1e-9, limit * .001)
    for _ in range(48):
        if high - low <= accuracy:
            break
        mid = (low + high) / 2
        if _line_within(a, b, mid):
            high = mid
        else:
            low = mid
    return vertex_lower, high


def _retreat_witness(a, b, upper, limit):
    """Locate the extremal segment through the continuous distance tube, not a grid."""
    import numpy as np
    import shapely
    threshold = max(0., upper-max(1e-9, limit*.002))
    candidates = []
    for source, target in ((a,b),(b,a)):
        outside = source.difference(target.buffer(threshold, quad_segs=32))
        samples = []
        for part in getattr(outside, "geoms", (outside,)):
            coordinates = shapely.get_coordinates(part)
            if not len(coordinates):
                continue
            samples.append(coordinates)
            if len(coordinates)>1 and part.geom_type in ("LineString", "LinearRing"):
                samples.append((coordinates[:-1]+coordinates[1:])/2)
        if not samples:
            continue
        points = np.concatenate(samples)
        distances = shapely.distance(shapely.points(points), target)
        i = int(np.argmax(distances))
        candidates.append((float(distances[i]),float(points[i,0]),float(points[i,1])))
    return max(candidates) if candidates else None


def _boundary_pairs(original, model):
    """Match each island exterior and hole; never match a hole to a board edge."""
    from shapely.geometry import Polygon
    targets = _components(model)
    pairs = []
    for source in _components(original):
        matches = [t for t in targets if source.covers(t.representative_point())]
        if len(matches) != 1:
            return None
        target = matches[0]
        pairs.append((source.exterior, target.exterior))
        holes = [Polygon(h) for h in target.interiors]
        if len(source.interiors) != len(holes):
            return None
        for hole in source.interiors:
            matches = [h for h in holes if h.covers(Polygon(hole))]
            if len(matches) != 1:
                return None
            pairs.append((hole, matches[0].exterior))
    if len(targets) != len(_components(original)):
        return None
    return pairs


def boundary_retreat(original, model, limit, *, measure=True):
    """Audit full corresponding contours of the UNION, excluding region seams."""
    pairs = _boundary_pairs(original, model)
    valid = pairs is not None and bool(pairs)
    within = valid and all(_line_within(a, b, limit) for a, b in pairs)
    bounds = [_line_bounds(a, b, limit) for a, b in pairs] if measure and valid else []
    worst = max(range(len(bounds)), key=lambda i:bounds[i][1]) if bounds else None
    witness = (_retreat_witness(*pairs[worst],bounds[worst][1],limit)
               if worst is not None and bounds[worst][1]>limit else None)
    return {"metric": "symmetric_euclidean_corresponding_contours",
            "max_allowed_mm": limit, "within_limit": within,
            "contour_pairs": len(pairs) if valid else 0,
            "max_distance_lower_mm": max((p[0] for p in bounds), default=None),
            "max_distance_upper_mm": max((p[1] for p in bounds), default=None),
            "worst_location_mm": list(witness[1:]) if witness else None,
            "measurement_resolution_mm": max(1e-9, limit * .001),
            "verification": "continuous two-sided inscribed distance tubes, quad_segs=32"}


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
        # Include endpoints: the tiny inward probes can round to a slab end
        # at large absolute coordinates. _edge_crossings applies the exact rule.
        edges = [edge for low,high,edge in self.edges[:bisect_right(self.starts,b)] if high >= a]
        return events,edges


def _envelope(region, a, b, pitch, origin, orthogonal, index=None):
    # Extrema at every vertex and both slab limits; edges are affine between
    # events. The common cross-section is conservative over the entire slab.
    events,edges = (index or _ProfileIndex(region)).slab(a,b)
    spans = None
    for left,right in zip(events, events[1:]):
        for x in (left + (right-left)*1e-8, right - (right-left)*1e-8):
            value = _edge_crossings(edges,x)
            spans = value if spans is None else _intersect_spans(spans,value)
    out = []
    for low, high in spans or []:
        if not orthogonal:
            low = origin + math.ceil((low-origin)/pitch-1e-7)*pitch
            high = origin + math.floor((high-origin)/pitch+1e-7)*pitch
        if high > low + 1e-8: out.append((round(low,9),round(high,9)))
    return out


def _pack(bands):
    """Merge neighboring vertical strips with identical complete Y envelopes."""
    merged=[]
    for a,b,spans in bands:
        if merged and merged[-1][2] == spans and abs(merged[-1][1]-a)<1e-8:
            merged[-1]=(merged[-1][0],b,spans)
        else:
            merged.append((a,b,spans))
    return [Rect(a,l,b,h) for a,b,spans in merged for l,h in spans]


def _coarsen(envelopes, original, allowed, step, origin, topology_source=None,
             max_boundary_retreat_mm=DEFAULT_MAX_BOUNDARY_RETREAT_MM):
    """Spend boundary-loss budget to reduce globally propagated axis lines.

    Every candidate is a subset of the already inscribed model. Narrow spans
    stay at their protected scale; vector topology gates every accepted change.
    """
    from shapely.geometry import box
    from shapely.ops import unary_union

    if topology_source is None:
        topology_source = _topology_source(original)

    def packed(bands):
        rects = _pack(bands)
        return rects, unary_union([box(*r.bounds) for r in rects])

    def score(bands):
        rects = _pack(bands)
        xs = {v for r in rects for v in (r.x0, r.x1)}
        ys = {v for r in rects for v in (r.y0, r.y1)}
        return (len(xs) + len(ys), len(rects))

    rejection_counts = {"area": 0, "boundary": 0, "topology": 0}

    def acceptable(bands, model=None):
        if model is None:
            _, model = packed(bands)
        if original.area - model.area > allowed + 1e-8 or model.difference(original).area > 1e-8:
            rejection_counts["area"] += 1
            return False
        if not boundary_retreat(original, model, max_boundary_retreat_mm, measure=False)["within_limit"]:
            rejection_counts["boundary"] += 1
            return False
        if not _topology(original, model, topology_source)["preserved"]:
            rejection_counts["topology"] += 1
            return False
        return True

    before = score(envelopes)
    chosen = envelopes
    chosen_pitch = None
    # One shared physical Y origin/pitch removes mixed refinement levels from
    # ordinary boundaries, without quantising away narrow connection spans.
    for pitch in (step / 2, step, step * 2, step * 4, step * 8):
        candidate = []
        for a, b, spans in envelopes:
            snapped = []
            for low, high in spans:
                lo = round(origin + math.ceil((low-origin)/pitch-1e-9)*pitch, 9)
                hi = round(origin + math.floor((high-origin)/pitch+1e-9)*pitch, 9)
                # Exact inward containment, including numerical rounding.
                lo, hi = max(low, lo), min(high, hi)
                snapped.append((lo, hi) if hi-lo >= pitch and high-low >= 2*pitch
                               else (low, high))
            candidate.append((a, b, snapped))
        if score(candidate) < score(chosen) and acceptable(candidate):
            chosen, chosen_pitch = candidate, pitch

    merged_pairs = 0
    rejected_topology = 0
    for _ in range(12):
        # Collapse equal envelopes before considering different Y boundaries.
        bands = []
        for a, b, spans in chosen:
            if bands and bands[-1][2] == spans and abs(bands[-1][1]-a) < 1e-8:
                bands[-1] = (bands[-1][0], b, spans)
            else:
                bands.append((a, b, spans))
        _, model = packed(bands)
        remaining = allowed - (original.area-model.area)
        proposals = []
        for i, (left, right) in enumerate(zip(bands, bands[1:])):
            a, b, ls = left
            c, d, rs = right
            spans = _intersect_spans(ls, rs)
            if abs(b-c) > 1e-8 or not ls or len(ls) != len(rs) or len(spans) != len(ls):
                continue
            cost = ((b-a)*sum(h-l for l,h in ls) + (d-c)*sum(h-l for l,h in rs)
                    - (d-a)*sum(h-l for l,h in spans))
            if cost <= remaining + 1e-8:
                proposals.append((cost, i, (a, d, spans)))
        selected = {}
        occupied = set()
        for cost, i, band in sorted(proposals):
            if i in occupied or i+1 in occupied or cost > remaining + 1e-8:
                continue
            selected[i] = band
            occupied.update((i, i+1))
            remaining -= cost
        if not selected:
            chosen = bands
            break
        candidate = [selected.get(i, band) for i,band in enumerate(bands)
                     if i-1 not in selected]
        if acceptable(candidate):
            chosen = candidate
            merged_pairs += len(selected)
        else:
            # A batch can cut a protected branch. Recheck individual changes;
            # reject them rather than spending area budget on topology loss.
            chosen = bands
            _, chosen_model = packed(chosen)
            accepted = 0
            for i, band in sorted(selected.items(), reverse=True):
                candidate = chosen[:i] + [band] + chosen[i+2:]
                removed = unary_union([box(a,l,b,h) for a,b,spans in chosen[i:i+2]
                                       for l,h in spans]).difference(
                    unary_union([box(band[0],l,band[1],h) for l,h in band[2]]))
                candidate_model = chosen_model.difference(removed)
                if acceptable(candidate, candidate_model):
                    chosen = candidate
                    chosen_model = candidate_model
                    accepted += 1
                else:
                    rejected_topology += 1
            merged_pairs += accepted
            if not accepted:
                break
    return chosen, {"objective": "reduce forced XY axes, then region count",
                    "shared_y_pitch_mm": chosen_pitch,
                    "axis_score_before": list(before),
                    "axis_score_after": list(score(chosen)),
                    "merged_adjacent_pairs": merged_pairs,
                    "topology_rejected_merges": rejected_topology,
                    "constraint_rejections": rejection_counts,
                    "all_constraints_rechecked": True}


def rectify_region(region, options=None, *, diagnostics=None):
    from shapely.geometry import box
    from shapely.ops import unary_union
    opts = options or RectifyOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()
    step = opts.tolerance_mm or DEFAULT_TOLERANCE_MM
    if not region or not region.bounds:
        diags.error(RECTIFY_EMPTY_PROFILE,"empty profile")
        return RectifyResult([],step,0,0,0,0,0,False)
    original = profile_shape(region)
    topology_source = _topology_source(original)
    profile_index = _ProfileIndex(region)
    x0,y0,x1,y1 = original.bounds
    if opts.tolerance_mm is None:
        basis = opts.basis_mm or original.bounds
        step = min(max(DEFAULT_TOLERANCE_MM, min(basis[2]-basis[0], basis[3]-basis[1])/32),
                   opts.max_boundary_retreat_mm/2, MAX_INITIAL_STEP_MM)
    orthogonal = all(abs(a-c)<1e-9 or abs(b-d)<1e-9 for r in region.rings
                     for (a,b),(c,d) in zip(tuple(r.points),tuple(r.points[1:])+tuple(r.points[:1])))
    if orthogonal:
        breaks = sorted({x for r in region.rings for x,y in r.points})
    else:
        bx0,by0,bx1,by1 = opts.basis_mm or original.bounds
        requested = (bx1-bx0)/step
        if not math.isfinite(requested) or requested > MAX_INITIAL_BANDS:
            diags.error(RECTIFY_AREA_ERROR,
                f"X interval [{bx0:g}, {bx1:g}] mm, length={bx1-bx0:g} mm: "
                f"retreat limit={opts.max_boundary_retreat_mm:g} mm requires {requested:g} "
                f"initial bands; internal resource limit={MAX_INITIAL_BANDS}")
            return RectifyResult([],step,original.area,0,original.area,0,1,False,
                                 audit={"termination":"initial band resource limit"})
        count = max(1,math.ceil(requested))
        breaks = [bx0+(bx1-bx0)*i/count for i in range(count+1)]
        y0 = by0
    allowed = opts.max_relative_area_error * original.area
    limit = opts.max_boundary_retreat_mm

    @dataclass
    class Band:
        a: float
        b: float
        pitch: float
        depth: int
        spans: list
        loss: float

        @property
        def envelope(self):
            return self.a, self.b, self.spans

    def make_band(a, b, pitch, depth):
        spans = _envelope(region, a, b, pitch, y0, orthogonal, profile_index)
        actual = original.intersection(box(a, y0-1, b, y1+1))
        actual = unary_union([p for p in _components(actual) if p.geom_type == "Polygon"])
        modeled = unary_union([box(a, low, b, high) for low, high in spans])
        loss = max(0., actual.area-modeled.area)
        return Band(a, b, pitch, depth, spans, loss)

    bands = [make_band(a, b, step, 0) for a, b in zip(breaks, breaks[1:])]
    history = []
    termination = "constraints_satisfied"
    for iteration in range(opts.max_refinement_steps+1):
        model = unary_union([box(*r.bounds) for r in _pack([p.envelope for p in bands])])
        contour = boundary_retreat(original, model, limit, measure=False)
        if not contour["within_limit"] and contour["contour_pairs"]:
            contour = boundary_retreat(original, model, limit)
        error = (limit if contour["within_limit"] else contour["max_distance_upper_mm"]) or 0.
        location = contour["worst_location_mm"]
        if not contour["contour_pairs"] and not model.is_empty:
            # Topology has not converged: use global union boundaries for a
            # spatial refinement witness; final acceptance still pairs contours.
            _, error = _line_bounds(original.boundary, model.boundary, limit)
            witness = _retreat_witness(original.boundary, model.boundary,error,limit)
            location = list(witness[1:]) if witness else None
        worst = max(bands, key=lambda p: (p.loss, -p.a))
        lost = sum(p.loss for p in bands)
        reason = "boundary" if error > limit else "area"
        if reason == "boundary" and location:
            x = location[0]
            candidates = [p for p in bands if p.a-1e-9<=x<=p.b+1e-9 and p.loss>1e-12]
            if candidates:
                worst = max(candidates, key=lambda p: (p.loss,-p.a))
        if error <= limit and lost <= allowed+1e-8:
            topology = _topology(original, model, topology_source)
            if topology["preserved"] and contour["within_limit"]:
                break
            reason = "topology" if not topology["preserved"] else "global_boundary"
        if reason != "boundary":
            candidates = ([p for p in bands if not p.spans and p.loss > 0]
                          if reason == "topology" else [])
            worst = max(candidates or bands, key=lambda p: (p.loss, -p.a))
        if (orthogonal or (allowed == 0 and not orthogonal)
                or worst.depth >= opts.max_refinement_rounds
                or worst.pitch/2 < opts.min_step_mm
                or (worst.b-worst.a)/2 < opts.min_step_mm
                or iteration == opts.max_refinement_steps or worst.loss <= 1e-12):
            termination = (f"cannot refine {reason}: X=[{worst.a:.9g}, {worst.b:.9g}] mm, "
                           f"length={worst.b-worst.a:.9g} mm, pitch={worst.pitch:.9g} mm, "
                           f"contour retreat upper={error:.9g} mm, "
                           f"limit={limit:g} mm, minimum step={opts.min_step_mm:g} mm, "
                           f"depth={worst.depth}/{opts.max_refinement_rounds}, "
                           f"steps={iteration}/{opts.max_refinement_steps}")
            break
        history.append({"iteration":iteration, "reason":reason,
                        "x_interval_mm":[worst.a,worst.b], "band_count":len(bands),
                        "selected_retreat_upper_mm":error,
                        "worst_retreat_upper_mm":error,
                        "worst_location_mm":location,
                        "lost_area_mm2":lost, "selected_loss_mm2":worst.loss})
        mid = (worst.a+worst.b)/2
        i = bands.index(worst)
        bands[i:i+1] = [make_band(worst.a, mid, worst.pitch/2, worst.depth+1),
                        make_band(mid, worst.b, worst.pitch/2, worst.depth+1)]
    envelopes = [p.envelope for p in bands]
    model = unary_union([box(*r.bounds) for r in _pack(envelopes)])
    topology = _topology(original, model, topology_source)
    lost = original.difference(model).area
    outside = model.difference(original).area
    contour = boundary_retreat(original, model, limit, measure=False)
    coarsening = {}
    coarsening_limit = min(limit,opts.coarsening_retreat_mm or limit)
    if topology["preserved"] and lost <= allowed+1e-8 and outside <= 1e-8 and contour["within_limit"]:
        origin = (opts.basis_mm or original.bounds)[1]
        envelopes, coarsening = _coarsen(envelopes, original, allowed, step, origin,
                                         topology_source, max_boundary_retreat_mm=coarsening_limit)
        model = unary_union([box(*r.bounds) for r in _pack(envelopes)])
        topology = _topology(original,model,topology_source)
        lost = original.difference(model).area
        outside = model.difference(original).area
    rectangles = _pack(envelopes)
    contour = boundary_retreat(original, model, limit)
    ok = (topology["preserved"] and lost <= allowed+1e-8 and outside <= 1e-8
          and contour["within_limit"])
    if not ok:
        diags.error(RECTIFY_AREA_ERROR,
                    f"profile bounds={original.bounds}: inscribed feature/area constraints failed; "
                    f"loss={lost:g} mm2 budget={allowed:g}; contour={contour}; "
                    f"topology={topology}; {termination}")
    rectified = union(Region([Ring(tuple(r.ring_points()),False) for r in rectangles]))
    return RectifyResult(rectangles,step,original.area,model.area,lost,outside,
                         lost/original.area,ok,net_area_error_mm2=model.area-original.area,
                         shape_deviation_mm2=lost+outside,region=rectified,
                         audit={"inscribed":outside <= 1e-8,"non_overlapping":
                                abs(sum(r.area for r in rectangles)-model.area)<1e-7,
                                "area_budget_mm2":allowed,
                                "relative_area_budget":opts.max_relative_area_error,
                                "boundary_retreat":contour,
                                "coarsening_retreat_limit_mm":coarsening_limit,
                                "min_step_mm":opts.min_step_mm,
                                "max_refinement_depth":opts.max_refinement_rounds,
                                "max_refinement_steps":opts.max_refinement_steps,
                                "max_initial_bands":MAX_INITIAL_BANDS,
                                "initial_seed_cap_mm":MAX_INITIAL_STEP_MM,
                                "refinement_priority":"worst full-contour retreat/limit first; then largest area loss",
                                "termination":termination,
                                "refinement_count":len(history),
                                "initial_scale_rule":"min(max(0.5 mm, shorter board side / 32), max boundary retreat / 2, 0.25 mm seed cap)",
                                "coarsening":coarsening,
                                "topology":topology,"local_refinement":history})

