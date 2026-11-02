"""Inscribed staircase planning in physical coordinates, independent of raster."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .geometry import Region
from .diagnostics import RECTIFY_AREA_ERROR, RECTIFY_EMPTY_PROFILE, Diagnostics

__all__ = ["Rect", "RectifyOptions", "RectifyResult", "rectify_region",
           "DEFAULT_TOLERANCE_MM", "perimeter"]

#: Initial physical X step; adaptive refinement never reads pixels.
DEFAULT_TOLERANCE_MM = 0.5
MAX_INITIAL_BANDS = 10000
# Keep the established topology-safe seed when users relax the error budget;
# coarsening spends only the area budget afterwards.
MAX_INITIAL_STEP_MM = 0.25
from .region_coarsen import _pack, _coarsen, MERGE_BOUNDARY_CLEARANCE_MM
from .region_profile import (perimeter, profile_shape, profile_region, _components,
                             _ProfileIndex, _envelope, _crossings, _intersect_spans)
from .region_topology import _topology_source, _covers_with_roundoff, _topology


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
    """One public area-loss budget, plus internal physical planning limits."""

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
    planning_area_ratio: Optional[float] = None
    reference_x_boundaries_mm: tuple[float, ...] = ()
    reference_y_boundaries_mm: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.planning_area_ratio is not None and (
                not math.isfinite(self.planning_area_ratio) or not 0 <= self.planning_area_ratio <= self.max_relative_area_error):
            raise ValueError("internal planning area ratio must be within the user area budget")
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


def rectify_region(region, options=None, *, diagnostics=None):
    from shapely.geometry import box
    from shapely.ops import unary_union
    opts = options or RectifyOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()
    step = opts.tolerance_mm or MAX_INITIAL_STEP_MM
    if not region or not region.bounds:
        diags.error(RECTIFY_EMPTY_PROFILE, "empty profile")
        return RectifyResult([],step,0,0,0,0,0,False)
    original = profile_shape(region)
    topology_source = _topology_source(original)
    profile_index = _ProfileIndex(region)
    x0,y0,x1,y1 = original.bounds
    orthogonal = all(abs(a-c)<1e-9 or abs(b-d)<1e-9 for r in region.rings
                     for (a,b),(c,d) in zip(tuple(r.points),tuple(r.points[1:])+tuple(r.points[:1])))
    if orthogonal:
        breaks = sorted({x for r in region.rings for x,y in r.points})
    else:
        bx0,_,bx1,_ = opts.basis_mm or original.bounds
        requested = (bx1-bx0)/step
        if not math.isfinite(requested) or requested > MAX_INITIAL_BANDS:
            diags.error(RECTIFY_AREA_ERROR, f"X=[{bx0:g},{bx1:g}] mm needs {requested:g} initial bands; limit={MAX_INITIAL_BANDS}")
            return RectifyResult([],step,original.area,0,original.area,0,1,False,
                                 audit={"termination":"initial band resource limit"})
        count = max(1,math.ceil(requested))
        breaks = [bx0+(bx1-bx0)*i/count for i in range(count+1)]
    ratio = opts.max_relative_area_error if opts.planning_area_ratio is None else opts.planning_area_ratio
    allowed = ratio * original.area

    @dataclass
    class Band:
        a: float
        b: float
        depth: int
        spans: list
        loss: float

        @property
        def envelope(self):
            return self.a, self.b, self.spans

    def make_band(a, b, depth):
        spans = _envelope(region,a,b,profile_index)
        actual = original.intersection(box(a,y0-1,b,y1+1))
        actual = unary_union([p for p in _components(actual) if p.geom_type == "Polygon"])
        modeled = unary_union([box(a,low,b,high) for low,high in spans])
        return Band(a,b,depth,spans,max(0.,actual.area-modeled.area))

    bands = [make_band(a,b,0) for a,b in zip(breaks,breaks[1:])]
    history = []
    termination = "constraints_satisfied"
    for iteration in range(opts.max_refinement_steps+1):
        model = unary_union([box(*r.bounds) for r in _pack([p.envelope for p in bands])])
        lost = sum(p.loss for p in bands)
        reason = "area"
        if lost <= allowed+1e-8:
            if _topology(original,model,topology_source)["preserved"]:
                break
            reason = "topology"
        candidates = [p for p in bands if not p.spans and p.loss>0] if reason == "topology" else []
        worst = max(candidates or bands,key=lambda p:(p.loss,-p.a))
        if (orthogonal or (allowed==0 and not orthogonal) or worst.depth>=opts.max_refinement_rounds
                or (worst.b-worst.a)/2<opts.min_step_mm or iteration==opts.max_refinement_steps or worst.loss<=1e-12):
            termination = (f"cannot refine {reason}: X=[{worst.a:.9g},{worst.b:.9g}] mm, "
                           f"length={worst.b-worst.a:.9g} mm, loss={lost:g}, budget={allowed:g}, "
                           f"depth={worst.depth}/{opts.max_refinement_rounds}, steps={iteration}/{opts.max_refinement_steps}")
            break
        history.append({"iteration":iteration,"reason":reason,"x_interval_mm":[worst.a,worst.b],
                        "band_count":len(bands),"lost_area_mm2":lost,"selected_loss_mm2":worst.loss})
        mid = (worst.a+worst.b)/2
        i = bands.index(worst)
        bands[i:i+1] = [make_band(worst.a,mid,worst.depth+1),make_band(mid,worst.b,worst.depth+1)]
    envelopes = [p.envelope for p in bands]
    model = unary_union([box(*r.bounds) for r in _pack(envelopes)])
    topology = _topology(original,model,topology_source)
    coarsening = {}
    if (topology["preserved"] and original.difference(model).area<=allowed+1e-8
            and model.difference(original).area<=1e-8):
        envelopes,coarsening = _coarsen(envelopes,original,allowed,topology_source,
            reference_x_boundaries_mm=opts.reference_x_boundaries_mm,
            reference_y_boundaries_mm=opts.reference_y_boundaries_mm)
        model = unary_union([box(*r.bounds) for r in _pack(envelopes)])
        topology = _topology(original,model,topology_source)
    rectangles = _pack(envelopes)
    lost,outside = original.difference(model).area,model.difference(original).area
    ok = topology["preserved"] and lost<=allowed+1e-8 and outside<=1e-8
    if not ok:
        diags.error(RECTIFY_AREA_ERROR,f"profile bounds={original.bounds}: loss={lost:g}, budget={allowed:g}; topology={topology}; {termination}")
    return RectifyResult(rectangles,step,original.area,model.area,lost,outside,lost/original.area,ok,
        net_area_error_mm2=model.area-original.area,shape_deviation_mm2=lost+outside,region=profile_region(model),
        audit={"inscribed":outside<=1e-8,"non_overlapping":abs(sum(r.area for r in rectangles)-model.area)<1e-7,
               "area_budget_mm2":allowed,"relative_area_budget":opts.max_relative_area_error,
               "planning_area_loss_ratio":ratio,"y_boundaries":"true_common_intervals","y_quantization":False,
               "min_step_mm":opts.min_step_mm,"max_refinement_depth":opts.max_refinement_rounds,
               "max_refinement_steps":opts.max_refinement_steps,"max_initial_bands":MAX_INITIAL_BANDS,
               "initial_seed_cap_mm":MAX_INITIAL_STEP_MM,"refinement_priority":"largest area loss, with topology protection",
               "termination":termination,"refinement_count":len(history),"coarsening":coarsening,
               "topology":topology,"local_refinement":history})
