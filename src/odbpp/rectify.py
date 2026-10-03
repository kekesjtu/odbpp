"""Inscribed staircase planning in physical coordinates, independent of raster."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .clipper import Region, Ring, union
from .diagnostics import RECTIFY_AREA_ERROR, RECTIFY_EMPTY_PROFILE, Diagnostics

__all__ = ["Rect", "RectifyOptions", "RectifyResult", "rectify_region",
           "DEFAULT_TOLERANCE_MM", "perimeter"]

#: Initial physical boundary step; adaptive refinement never reads pixels.
DEFAULT_TOLERANCE_MM = 0.5


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
    """Internal physical geometry policy; never derived from pixels."""

    tolerance_mm: Optional[float] = None
    #: Reject the result when lost or gained area exceeds this fraction of the
    #: original outline area.
    max_relative_area_error: float = 0.05
    #: Optional absolute floor for internal diagnostics. Disabled by default so
    #: the 5% limit applies even to small boards.
    max_absolute_area_error_mm2: float = 0.0
    min_step_mm: float = 0.0625
    max_refinement_rounds: int = 8
    basis_mm: Optional[tuple[float, float, float, float]] = None

    def __post_init__(self) -> None:
        if self.tolerance_mm is not None and (not math.isfinite(self.tolerance_mm) or self.tolerance_mm <= 0.0):
            raise ValueError(f"tolerance_mm must be positive, got {self.tolerance_mm!r}")
        if not math.isfinite(self.max_relative_area_error) or self.max_relative_area_error < 0.0:
            raise ValueError("max_relative_area_error cannot be negative")
        if not math.isfinite(self.max_absolute_area_error_mm2) or self.max_absolute_area_error_mm2 < 0.0:
            raise ValueError("max_absolute_area_error_mm2 must be finite and nonnegative")
        if not math.isfinite(self.min_step_mm) or self.min_step_mm <= 0:
            raise ValueError("min_step_mm must be finite and positive")


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
            "strategy": "inscribed-vertical-staircase-v3",
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
    return [] if shape.is_empty else ([shape] if shape.geom_type == "Polygon" else list(shape.geoms))


def _topology(original, model):
    from shapely.geometry import Polygon
    sources, targets = _components(original), _components(model)
    matches = [sum(s.covers(t.representative_point()) for t in targets) for s in sources]
    holes = [Polygon(h) for s in sources for h in s.interiors]
    model_holes = [Polygon(h) for t in targets for h in t.interiors]
    hole_matches = [sum(m.covers(h) for m in model_holes) for h in holes]
    positive = all(len(_components(t.buffer(-1e-7))) == 1 for t in targets)
    # Erosion splits lobes separated by a narrow neck. Preserve every such
    # geometric core, including terminal branches within a single source island.
    # These scales are physical millimetres, entirely independent of sampling.
    core_checks = []
    for radius in (0.03125, 0.1, 0.25, 0.5, 1.0):
        cores = _components(original.buffer(-radius))
        kept = [not c.intersection(model).is_empty and c.intersection(model).area > 1e-10
                for c in cores]
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
    ys = []
    for ring in rings:
        pts = tuple(ring.points)
        for (x0,y0),(x1,y1) in zip(pts, pts[1:]+pts[:1]):
            if (x0 <= x) != (x1 <= x):
                ys.append(y0+(y1-y0)*(x-x0)/(x1-x0))
    ys.sort()
    return list(zip(ys[::2], ys[1::2]))


def _envelope(region, a, b, pitch, origin, orthogonal):
    # Extrema at every vertex and both slab limits; edges are affine between
    # events. The common cross-section is conservative over the entire slab.
    events = sorted({a,b} | {x for r in region.rings for x,y in r.points if a < x < b})
    spans = None
    for left,right in zip(events, events[1:]):
        for x in (left + (right-left)*1e-8, right - (right-left)*1e-8):
            value = _crossings(region.rings,x)
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


def _coarsen(envelopes, original, allowed, step, origin):
    """Spend boundary-loss budget to reduce globally propagated axis lines.

    Every candidate is a subset of the already inscribed model. Narrow spans
    stay at their protected scale; vector topology gates every accepted change.
    """
    from shapely.geometry import box
    from shapely.ops import unary_union

    def packed(bands):
        rects = _pack(bands)
        return rects, unary_union([box(*r.bounds) for r in rects])

    def score(bands):
        rects = _pack(bands)
        xs = {v for r in rects for v in (r.x0, r.x1)}
        ys = {v for r in rects for v in (r.y0, r.y1)}
        return (len(xs) + len(ys), len(rects))

    def acceptable(bands):
        _, model = packed(bands)
        return (original.area - model.area <= allowed + 1e-8
                and model.difference(original).area <= 1e-8
                and _topology(original, model)["preserved"])

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
            accepted = 0
            for i, band in sorted(selected.items(), reverse=True):
                candidate = chosen[:i] + [band] + chosen[i+2:]
                if acceptable(candidate):
                    chosen = candidate
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
                    "topology_rejected_merges": rejected_topology}


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
    x0,y0,x1,y1 = original.bounds
    if opts.tolerance_mm is None:
        basis = opts.basis_mm or original.bounds
        step = max(DEFAULT_TOLERANCE_MM, min(basis[2]-basis[0], basis[3]-basis[1])/32)
    orthogonal = all(abs(a-c)<1e-9 or abs(b-d)<1e-9 for r in region.rings
                     for (a,b),(c,d) in zip(tuple(r.points),tuple(r.points[1:])+tuple(r.points[:1])))
    if orthogonal:
        breaks = sorted({x for r in region.rings for x,y in r.points})
    else:
        bx0,by0,bx1,by1 = opts.basis_mm or original.bounds
        count = max(1,math.ceil((bx1-bx0)/step))
        breaks = [bx0+(bx1-bx0)*i/count for i in range(count+1)]
        y0 = by0
    bands = [(a,b,step) for a,b in zip(breaks,breaks[1:])]
    allowed = max(opts.max_absolute_area_error_mm2,opts.max_relative_area_error*original.area)
    history = []
    for iteration in range(opts.max_refinement_rounds+1):
        envelopes = [(a,b,_envelope(region,a,b,pitch,y0,orthogonal)) for a,b,pitch in bands]
        slab_rects = [Rect(a,l,b,h) for a,b,spans in envelopes for l,h in spans]
        model = unary_union([box(*r.bounds) for r in slab_rects])
        topology = _topology(original,model)
        lost = original.difference(model).area
        outside = model.difference(original).area
        history.append({"round":iteration,"band_count":len(bands),"lost_area_mm2":lost,
                        "topology_preserved":topology["preserved"]})
        if topology["preserved"] and lost <= allowed and outside <= 1e-8: break
        refined,changed = [],False
        for (a,b,pitch),(_,_,spans) in zip(bands,envelopes):
            covered = sum((h-l)*(b-a) for l,h in spans)
            actual = original.intersection(box(a,y0,b,y1)).area
            if actual-covered > 1e-8 and pitch/2 >= opts.min_step_mm-1e-9 and not orthogonal:
                mid=(a+b)/2
                refined.extend([(a,mid,pitch/2),(mid,b,pitch/2)])
                changed=True
            else: refined.append((a,b,pitch))
        if not changed: break
        bands=refined
    coarsening = {}
    if topology["preserved"] and lost <= allowed and outside <= 1e-8:
        origin = (opts.basis_mm or original.bounds)[1]
        envelopes, coarsening = _coarsen(envelopes, original, allowed, step, origin)
        model = unary_union([box(*r.bounds) for r in _pack(envelopes)])
        topology = _topology(original,model)
        lost = original.difference(model).area
        outside = model.difference(original).area
    rectangles = _pack(envelopes)
    ok = topology["preserved"] and lost <= allowed+1e-8 and outside <= 1e-8
    if not ok:
        diags.error(RECTIFY_AREA_ERROR,
                    f"profile bounds={original.bounds}: inscribed feature/area constraints failed; "
                    f"loss={lost:g} mm2 budget={allowed:g}; topology={topology}; "
                    f"minimum internal step={opts.min_step_mm:g} mm")
    rectified = union(Region([Ring(tuple(r.ring_points()),False) for r in rectangles]))
    return RectifyResult(rectangles,step,original.area,model.area,lost,outside,
                         lost/original.area,ok,net_area_error_mm2=model.area-original.area,
                         shape_deviation_mm2=lost+outside,region=rectified,
                         audit={"inscribed":outside <= 1e-8,"non_overlapping":
                                abs(sum(r.area for r in rectangles)-model.area)<1e-7,
                                "area_budget_mm2":allowed,
                                "relative_area_budget":opts.max_relative_area_error,
                                "min_step_mm":opts.min_step_mm,
                                "initial_scale_rule":"max(0.5 mm, shorter board side / 32)",
                                "coarsening":coarsening,
                                "topology":topology,"local_refinement":history})


