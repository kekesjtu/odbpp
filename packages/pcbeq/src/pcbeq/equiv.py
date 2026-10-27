"""PCB equivalence: independent inscribed geometry and per-layer raster sampling.
Properties apply to complete cells and complete physical layer Z intervals.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence

from .geometry import Region, region_from_rings
from .diagnostics import (
    EQUIV_DRILL_SPAN_UNKNOWN,
    EQUIV_DRILL_Z_FALLBACK,
    EQUIV_DRILL_Z_MISMATCH,
    EQUIV_LAYER_EMPTY,
    EQUIV_LAYER_FAILED,
    EQUIV_LAYER_SKIPPED,
    EQUIV_LAYER_Z_UNKNOWN,
    EQUIV_MISSING_DOMAIN,
    GRID_CELL_WITHOUT_PIXEL,
    Diagnostics,
)
from .errors import EquivalenceErrorBase
from .intermediate import BoardDocument, validate_document
from pydantic import ValidationError
from .grid import BoardGrid, GridOptions, build_grid
from .materials import MaterialBook, ResolvedMaterials
from .raster import LayerMask, RasterAxis, region_edges
from .rectify import (Rect, RectifyOptions, RectifyResult, rectify_region,
                      profile_shape, _topology, boundary_retreat)

__all__ = [
    "EquivalenceOptions",
    "EquivalentCell",
    "LayerReport",
    "EquivalenceResult",
    "equivalence_from_document",
    "rectify_step_outline",
    "write_materialmap",
    "write_run_report",
]

#: Layer types that take the copper/base model in this first version.
COPPER_TYPES = frozenset({"SIGNAL", "POWER_GROUND", "MIXED"})
DIELECTRIC_TYPE = "DIELECTRIC"

#: Types deliberately excluded, with the reason recorded in the metadata.
SKIP_REASONS = {
    "SOLDER_MASK": "solder mask is not covered by the copper/FR4 model in v1",
    "SOLDER_PASTE": "solder paste is not covered by the copper/FR4 model in v1",
    "SILK_SCREEN": "silkscreen is not covered by the copper/FR4 model in v1",
    "CONDUCTIVE_PASTE": "conductive paste is not covered by the copper/FR4 model in v1",
    "MASK": "mask is not covered by the copper/FR4 model in v1",
    "DRILL": "drill is a cross-layer pattern, projected into the layers it "
             "crosses as solid copper; it is not its own thickness layer",
    "ROUT": "rout is a milling contour, not a drill; it carries no equivalence",
    "DOCUMENT": "documentation is not a physical layer",
    "COMPONENT": "component outlines are not part of the board laminate",
}

_EPS = 1e-9


def pipeline_fingerprint() -> str:
    """Invalidate batch output reuse when a participating implementation changes."""
    import hashlib
    from pathlib import Path
    digest = hashlib.sha256()
    for name in ("equiv.py", "geometry.py", "rectify.py", "grid.py", "raster.py",
                 "materials.py", "visualize.py", "mesh.py", "intermediate.py", "materialmap.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


class EquivalenceError(EquivalenceErrorBase):
    """The input document cannot be equivalented at all."""


@dataclass(frozen=True)
class EquivalenceOptions:
    grid: GridOptions
    materials: MaterialBook = field(default_factory=MaterialBook)
    rectify: RectifyOptions = field(default_factory=RectifyOptions)


@dataclass(frozen=True, slots=True)
class EquivalentCell:
    layer_name: str
    layer_type: str
    region_id: int
    i: int
    j: int
    x0: float
    y0: float
    x1: float
    y1: float
    z_start_mm: float
    z_end_mm: float
    thickness_known: bool
    copper_fraction: float
    kx: float
    ky: float
    kz: float
    density: float
    specific_heat: float
    pixel_rows: int
    pixel_cols: int
    pixel_count: int
    cell_area_mm2: float
    domain_area_mm2: float
    domain_coverage: float
    partial_domain: bool

    def property_values(self) -> list[float]:
        """The sole serialized cell payload; preserve former published precision."""
        values = (self.kx, self.ky, self.kz, self.density, self.specific_heat)
        return [rounded if rounded > 0 else value for value, digits in zip(values, (6,6,6,4,4))
                for rounded in (_r(value, digits),)]


@dataclass
class LayerReport:
    """What happened to one board layer, eligible or not."""

    name: str
    type: str
    status: str                      # "equivalent" | "skipped" | "failed"
    reason: Optional[str] = None
    domain_id: Optional[str] = None
    domain_source: Optional[str] = None
    z_start_mm: Optional[float] = None
    z_end_mm: Optional[float] = None
    thickness_known: bool = False
    drill_overlays: list[str] = field(default_factory=list)
    cell_count: int = 0
    partial_cells: int = 0
    z_provenance: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "reason": self.reason,
            "domain_id": self.domain_id,
            "domain_source": self.domain_source,
            "z_mm": None if self.z_start_mm is None else {
                "start_mm": self.z_start_mm,
                "end_mm": self.z_end_mm,
                "thickness_mm": (self.z_end_mm - self.z_start_mm)
                if self.z_end_mm is not None else None,
                "thickness_known": self.thickness_known,
                **self.z_provenance,
            },
            "drill_overlays": list(self.drill_overlays),
            "cell_count": self.cell_count,
            "partial_domain_cells": self.partial_cells,
        }


@dataclass
class EquivalenceResult:
    cells: list[EquivalentCell]
    layers: list[LayerReport]
    grid: BoardGrid
    rectification: RectifyResult
    raster_px: int
    raster_py: int
    pixel_step_mm: tuple[float, float]
    board_bounds_mm: Optional[tuple[float, float, float, float]]
    diagnostics: Diagnostics
    materials_by_layer: dict[str, dict] = field(default_factory=dict)
    cell_without_pixel: int = 0
    #: True when a problem makes the whole run untrustworthy (unknown drill span,
    #: unsupported schema, ...).  The caller must not write results silently.
    fatal: bool = False

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    def metadata(self, *, source_path: Optional[str] = None,
                 step_name: Optional[str] = None) -> dict:
        return {
            "pipeline_fingerprint": pipeline_fingerprint(),
            "source": {
                "path": source_path,
                "step": step_name,
            },
            "units": {
                "length": "mm",
                "area": "mm2",
                "thermal_conductivity": "W/(m*K)",
                "density": "kg/m3",
                "specific_heat": "J/(kg*K)",
            },
            "z_axis": "increases downward from the top of the stack, mm",
            "drill_model": "solid_copper",
            "drill_scope": "dielectric_only",
            "coverage": {
                "columns": ["cell_area_mm2", "domain_area_mm2",
                            "domain_coverage"],
                "semantics": "All properties apply to the complete region-contained "
                             "cell volume. domain_area_mm2 equals cell_area_mm2; "
                             "domain_coverage=1 is a geometric invariant, never a mass multiplier. "
                             "Profile approximation loss is recorded separately.",
            },
            "raster": {
                "px": self.raster_px,
                "py": self.raster_py,
                "pixel_step_mm": list(self.pixel_step_mm),
                "board_bounds_mm": None if self.board_bounds_mm is None
                else list(self.board_bounds_mm),
                "sampling": "pixel centres (half-open cell intervals)",
            },
            "grid": self.grid.as_dict(),
            "rectification": self.rectification.as_dict(),
            "materials": {
                "defaults": self.materials_by_layer.get("__defaults__", {}),
                "by_layer": {
                    name: info for name, info in self.materials_by_layer.items()
                    if name != "__defaults__"
                },
            },
            "layers": [report.as_dict() for report in self.layers],
            "diagnostics": [
                {"level": d.level, "code": d.code, "message": d.message,
                 "path": d.path}
                for d in self.diagnostics
            ],
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _r(value: float, digits: int = 4) -> float:
    out = round(float(value) + 0.0, digits)
    return 0.0 if out == 0.0 else out


def _region_from_polygons(polygons: Sequence[Mapping[str, Any]]) -> Region:
    rings: list[tuple[Sequence[tuple[float, float]], bool]] = []
    for polygon in polygons or ():
        points = [(float(x), float(y)) for x, y in polygon.get("points", ())]
        rings.append((points, not bool(polygon.get("island", True))))
    return region_from_rings(rings)


def rectify_step_outline(step: Mapping[str, Any], options: RectifyOptions, *,
                         diagnostics: Optional[Diagnostics] = None) -> RectifyResult:
    """Prepare the same outline for equivalence and batch grid planning."""
    step_domain = _domain_index(step).get(step.get("profile_domain_id"))
    if step_domain is None:
        raise EquivalenceError(
            f"step {step.get('name')!r} has no step_profile domain; the board outline "
            "is required to size the raster and rectangularise the board")
    profile = _region_from_polygons(step_domain.get("polygons"))
    return rectify_region(profile, options, diagnostics=diagnostics)


def _overlaps(layer_start: float, layer_end: float,
              drill_start: float, drill_end: float) -> bool:
    """Half-open Z overlap, with a point test for a zero-thickness layer.

    Half-open is what keeps a drill that *ends* at a copper plane from also
    perforating the dielectric below it.  A copper layer whose thickness the
    format never stated has zero thickness here; it is treated as the plane it
    is, so a drill reaching that plane still overlays it.
    """
    if layer_end - layer_start <= _EPS:
        return drill_start - _EPS <= layer_start <= drill_end + _EPS
    return drill_start < layer_end - _EPS and layer_start < drill_end - _EPS


def _single_step(document: Mapping[str, Any]) -> Mapping[str, Any]:
    steps = document.get("steps") or []
    if len(steps) != 1:
        raise EquivalenceError(
            f"the preview document must contain exactly one step, found {len(steps)}; "
            f"AGENTS constraint 1 excludes panel arrays")
    return steps[0]


def _domain_index(step: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {d.get("id"): d for d in (step.get("domains") or ()) if d.get("id")}


def _layer_index(step: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {l.get("name"): l for l in (step.get("layers") or ())}


# ---------------------------------------------------------------------------
# drill projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Drill:
    name: str
    z_start_mm: float
    z_end_mm: float
    region: Region
    #: Resolved copper-plane interval. Absolute Z above is derived internally
    #: from these endpoint copper layers, never read from a DRILL JSON record.
    copper_span: tuple[int, int]


def _copper_span(row: Mapping[str, Any]) -> Optional[tuple[int, int]]:
    """Read the sole resolved copper-plane span from a layer record."""
    index = row.get("copper_span")
    if isinstance(index, (list, tuple)) and len(index) == 2:
        try:
            return (int(index[0]), int(index[1]))
        except (TypeError, ValueError):
            return None
    return None


def _collect_drills(step: Mapping[str, Any],
                    diagnostics: Diagnostics) -> tuple[list[_Drill], bool]:
    """Resolve drill depth from copper_span and endpoint copper layers only."""
    layers = _layer_index(step)
    copper = {row["copper_index"]: row for row in layers.values()
              if row.get("copper_index") is not None}
    drills: list[_Drill] = []
    fatal = False
    for row in step.get("layers") or ():
        if row.get("type") != "DRILL" or row.get("context", "BOARD") != "BOARD":
            continue
        name = row.get("name", "?")
        span = _copper_span(row)
        if span is None:
            diagnostics.error(
                EQUIV_DRILL_SPAN_UNKNOWN,
                f"drill layer {name!r} has no resolvable copper_span "
                f"(START_NAME/END_NAME do not resolve to copper layers); which "
                f"layers it crosses cannot be determined, so its solid-copper "
                f"projection is unknown. Refusing to guess a through-hole.",
            )
            fatal = True
            continue
        top = copper.get(span[0], {}).get("z_mm")
        bottom = copper.get(span[1], {}).get("z_mm")
        if not top or not bottom:
            diagnostics.error(
                EQUIV_DRILL_SPAN_UNKNOWN,
                f"drill layer {name!r}: copper_span {list(span)} endpoint copper "
                "layer is missing z_mm; its absolute depth cannot be derived",
            )
            fatal = True
            continue
        layer = layers.get(name)
        if layer is None or layer.get("geometry_status", "ready") != "ready":
            diagnostics.error(
                EQUIV_DRILL_SPAN_UNKNOWN,
                f"drill layer {name!r} is in the stack but has no geometry in "
                f"the preview; its projection cannot be computed",
            )
            fatal = True
            continue
        drills.append(_Drill(name=name,
                             z_start_mm=float(top["start_mm"]),
                             z_end_mm=float(bottom["end_mm"]),
                             region=_region_from_polygons(layer.get("polygons")),
                             copper_span=span))
    return drills, fatal


def _topological_overlap(layer_doc: Mapping[str, Any],
                         drill: _Drill) -> Optional[bool]:
    """Decide drill/layer overlap from copper-plane indices, or ``None``.

    Absolute Z is a derived quantity: when a stack layer has no stated
    thickness, several layers can share a best-effort ``start_mm == end_mm``,
    and matching by Z would then mark a layer the drill never reaches.  The
    copper-plane index is the topology the format authored, so it wins whenever
    both sides expose one. Only dielectric intervals participate; copper is
    entirely independent of drill projections.
    """
    span = drill.copper_span
    start, end = span
    index = layer_doc.get("copper_span")
    if not isinstance(index, (list, tuple)) or len(index) != 2:
        return None
    return max(int(index[0]), start) < min(int(index[1]), end)


# ---------------------------------------------------------------------------
# per-layer evaluation
# ---------------------------------------------------------------------------

class _Scanline:
    """Active-edge scanline index over a fixed edge set.

    Rows must be queried in non-decreasing ``y``.  Each edge enters the active
    set when the scanline reaches its lower end and leaves when it passes its
    upper end, so a 30k-polygon layer costs O(edges) setup rather than O(edges)
    per pixel row.
    """

    def __init__(self, edges: Sequence[tuple[float, float, float, float]]) -> None:
        # Horizontal edges never cross. Precompute bounds and deltas once,
        # retaining the original interpolation arithmetic (no slope rounding).
        self._edges = sorted(
            ((min(y1, y2), max(y1, y2), x1, y1, x2-x1, y2-y1)
             for x1, y1, x2, y2 in edges if y1 != y2), key=lambda e: e[0])
        self._cursor = 0
        self._active: list[tuple] = []

    def spans_at(self, y: float) -> list[tuple[float, float]]:
        edges = self._edges
        while self._cursor < len(edges):
            edge = edges[self._cursor]
            if edge[0] <= y:
                self._active.append(edge)
                self._cursor += 1
            else:
                break
        active = [edge for edge in self._active if edge[1] > y]
        self._active = active
        crossings: list[float] = []
        for low, high, x1, y1, dx, dy in active:
            t = (y - y1) / dy
            crossings.append(x1 + dx * t)
        if len(crossings) < 2:
            return []
        crossings.sort()
        spans: list[tuple[float, float]] = []
        for k in range(0, len(crossings) - 1, 2):
            left, right = crossings[k], crossings[k + 1]
            if right > left:
                spans.append((left, right))
        return spans



def _merge_spans(spans):
    out = []
    for a,b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1]=(out[-1][0],max(b,out[-1][1]))
        else: out.append((a,b))
    return out


def _evaluate_layer(name, type_name, domain, black, z_start, z_end,
                    thickness_known, materials, grid, x_axis, y_axis, diagnostics,
                    emit, *, mask=None, step_domain=None,
                    sampling_plan=None):
    """Independent full-board scanlines; integrate complete Cartesian cells."""
    import numpy as np
    from .rectify import _intersect_spans
    # Exactly one layer resident. Complex material patterns are never booleaned.
    raster = mask if mask is not None else LayerMask(x_axis.count,y_axis.count)
    domain_scan = _Scanline(region_edges(domain))
    step_scan = _Scanline(region_edges(step_domain)) if step_domain is not None else None
    scans = [_Scanline(region_edges(r)) for r in black if r]
    for b in range(y_axis.count):
        y = y_axis.centre(b)
        dom = domain_scan.spans_at(y)
        if step_scan is not None:
            dom = _intersect_spans(dom, step_scan.spans_at(y))
        ink = _merge_spans([span for scan in scans for span in scan.spans_at(y)])
        raster.set_spans(b,dom,_intersect_spans(dom,ink),x_axis)
    pixels=np.frombuffer(raster.pixels,dtype=np.uint8).reshape(y_axis.count,x_axis.count)
    k1,k2=materials.k1,materials.k2
    d1,c1=materials.black.density_kg_m3,materials.black.specific_heat_j_kgk
    d2,c2=materials.white.density_kg_m3,materials.white.specific_heat_j_kgk
    emitted=no_pixel=0
    minimum_samples=(grid.mesh_configuration or {}).get("minimum_samples_per_axis",1)
    # Row-prefix integration avoids rescanning wide regions for each global cell.
    if sampling_plan is None:
        sampling_plan = _sampling_plan(grid, x_axis, y_axis)
    for b0, b1, samples, xlo, xhi in sampling_plan:
        band=pixels[b0:b1,xlo:xhi]
        ink=band == LayerMask.BLACK
        prefix=np.zeros((b1-b0,xhi-xlo+1),dtype=np.uint32)
        np.cumsum(ink,axis=1,dtype=np.uint32,out=prefix[:,1:])
        col_counts=ink.sum(axis=0,dtype=np.uint32)
        outside=(band == LayerMask.OUTSIDE).any(axis=0)
        for cell, a0, a1 in samples:
            cols,rows=a1-a0,b1-b0
            a0, a1 = a0-xlo, a1-xlo
            if min(cols,rows)<minimum_samples or outside[a0:a1].any():
                no_pixel+=1
                if no_pixel<=20:
                    diagnostics.error(GRID_CELL_WITHOUT_PIXEL,
                        f"layer {name!r} region {cell.region_id} cell ({cell.i},{cell.j}) "
                        f"bounds={cell.bounds}: samples={cols}x{rows}, required per axis={minimum_samples}; "
                        "insufficient raster samples or domain/geometry contradiction; increase px/py; geometry is unchanged")
                continue
            row_phi=(prefix[:,a1]-prefix[:,a0])/cols
            col_phi=col_counts[a0:a1]/rows
            phi=float(row_phi.mean())
            kx=float(np.mean(1/(row_phi/k1+(1-row_phi)/k2)))
            ky=float(np.mean(1/(col_phi/k1+(1-col_phi)/k2)))
            density=phi*d1+(1-phi)*d2
            cp=(phi*d1*c1+(1-phi)*d2*c2)/density
            emit(EquivalentCell(name,type_name,cell.region_id,cell.i,cell.j,
                 cell.rect.x0,cell.rect.y0,cell.rect.x1,cell.rect.y1,z_start,z_end,
                 thickness_known,phi,kx,ky,phi*k1+(1-phi)*k2,density,cp,
                 rows,cols,rows*cols,cell.rect.area,cell.rect.area,1.0,False))
            emitted+=1
    if no_pixel>20:
        diagnostics.error(GRID_CELL_WITHOUT_PIXEL,f"layer {name!r}: {no_pixel} cells have insufficient sampling; first 20 locations reported")
    return emitted,0,no_pixel


def _sampling_plan(grid, x_axis, y_axis):
    """Reuse only pixel indices for a fixed domain grid and fixed raster."""
    bands = {}
    x_ranges = {}
    for cell in grid.cells:
        if cell.i not in x_ranges:
            x_ranges[cell.i] = x_axis.range_for(cell.rect.x0, cell.rect.x1)
        a0, a1 = x_ranges[cell.i]
        bands.setdefault(cell.j, []).append((cell, a0, a1))
    plan = []
    for samples in bands.values():
        first = samples[0][0]
        b0, b1 = y_axis.range_for(first.rect.y0, first.rect.y1)
        plan.append((b0, b1, samples, min(s[1] for s in samples),
                     max(s[2] for s in samples)))
    return plan


def plan_geometry(step, options, diagnostics):
    """Retry shared-domain failures with less coarsening, never relaxed budgets."""
    attempts = []
    for divisor in (1,2,4,8,16):
        cap = min(options.rectify.max_boundary_retreat_mm,
                  options.rectify.coarsening_retreat_mm or options.rectify.max_boundary_retreat_mm)/divisor
        attempt = replace(options,rectify=replace(options.rectify,coarsening_retreat_mm=cap))
        local = Diagnostics()
        geometry,membership = _plan_geometry_once(step,attempt,local)
        attempts.append({"coarsening_retreat_limit_mm":cap,"ok":geometry.ok,
            "failed_domains":[key for key,audit in geometry.audit.get("domains",{}).items()
                              if not audit["ok"]]})
        if (geometry.ok or not local.errors
                or any(d.code != "equiv.domain_geometry" for d in local.errors)):
            break
    geometry.audit["shared_domain_replanning"] = attempts
    diagnostics.extend(local)
    return geometry,membership


def _plan_geometry_once(step, options, diagnostics):
    """Common physical XY partition and explicit layer-domain membership, no pixels."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    rectification=rectify_step_outline(step,options.rectify,diagnostics=diagnostics)
    domains=_domain_index(step)
    profile_id=step.get("profile_domain_id")
    ids={row.get("domain_id") for row in step.get("layers",[])
         if row.get("context", "BOARD")=="BOARD" and
         (row.get("type") in COPPER_TYPES or row.get("type")==DIELECTRIC_TYPE)}

    step_original=profile_shape(_region_from_polygons(domains[profile_id].get("polygons")))
    basis_options=replace(options.rectify,basis_mm=step_original.bounds)
    originals={profile_id:step_original}
    plans={profile_id:rectification}
    effective_regions={profile_id:_region_from_polygons(domains[profile_id].get("polygons"))}
    from .geometry import region_from_rings
    from copy import deepcopy
    dedup={step_original.wkb:rectification}
    for domain_id in sorted(ids-{profile_id,None}):
        if domain_id not in domains: continue
        authored=profile_shape(_region_from_polygons(domains[domain_id].get("polygons")))
        original=authored.intersection(step_original)
        if original.is_empty or original.area <= 0:
            raise EquivalenceError(f"domain {domain_id!r} has no physical area within the step profile")
        originals[domain_id]=original
        components=[original] if original.geom_type=="Polygon" else list(original.geoms)
        region=region_from_rings([(tuple(p.exterior.coords)[:-1],False) for p in components]+
                                [(tuple(h.coords)[:-1],True) for p in components for h in p.interiors])
        effective_regions[domain_id]=region
        if original.wkb not in dedup:
            dedup[original.wkb]=rectify_region(region,basis_options,diagnostics=diagnostics)
        plans[domain_id]=dedup[original.wkb]
    rectangles=list(rectification.rectangles)
    step_model=unary_union([box(*r.bounds) for r in rectangles])
    audit={}
    models={}
    processed=set()
    for domain_id,plan in plans.items():
        original=originals[domain_id]
        model=unary_union([box(*r.bounds) for r in plan.rectangles]).intersection(step_model)
        topology=_topology(original,model)
        loss=original.area-model.area
        allowed=plan.audit.get("area_budget_mm2",0)
        contour=boundary_retreat(original,model,options.rectify.max_boundary_retreat_mm)
        good=plan.ok and topology["preserved"] and loss <= allowed+1e-8 and contour["within_limit"]
        if not good:
            diagnostics.error("equiv.domain_geometry",f"domain {domain_id!r}: shared step/layer "
                f"model failed; loss={loss:g} budget={allowed:g}; "
                f"boundary_retreat={contour}; topology={topology}")
            rectification.ok=False
        authored=profile_shape(_region_from_polygons(domains[domain_id].get("polygons")))
        audit[domain_id]={**deepcopy(plan.as_dict()),"authored_area_mm2":authored.area,
                          "outside_step_area_mm2":authored.difference(step_original).area,
                          "effective_domain":"layer profile AND original step profile",
                          "shared_model_area_mm2":model.area,
                          "shared_loss_area_mm2":loss,"shared_topology":topology,
                          "shared_boundary_retreat":contour,"ok":good}
        models[domain_id]=model
        if model.wkb==step_model.wkb or model.wkb in processed: continue
        processed.add(model.wkb)
        for cut in plan.rectangles:
            pieces=[]
            for r in rectangles:
                xa,ya,xb,yb=max(r.x0,cut.x0),max(r.y0,cut.y0),min(r.x1,cut.x1),min(r.y1,cut.y1)
                if xb-xa <= 1e-9 or yb-ya <= 1e-9:
                    pieces.append(r);continue
                pieces.append(Rect(xa,ya,xb,yb))
                if xa>r.x0+1e-9: pieces.append(Rect(r.x0,r.y0,xa,r.y1))
                if xb<r.x1-1e-9: pieces.append(Rect(xb,r.y0,r.x1,r.y1))
                if ya>r.y0+1e-9: pieces.append(Rect(xa,r.y0,xb,ya))
                if yb<r.y1-1e-9: pieces.append(Rect(xa,yb,xb,r.y1))
            rectangles=pieces
    rectangles=sorted(rectangles,key=lambda r:r.bounds)
    membership={key:[] for key in models}
    for rid,r in enumerate(rectangles):
        shape=box(*r.bounds)
        for key,model in models.items():
            if model.covers(shape): membership[key].append(rid)
            elif model.intersection(shape).area > 1e-8:
                diagnostics.error("equiv.domain_partition",f"domain {key!r} partially covers region {rid} {r.bounds}")
                rectification.ok=False
    rectification.rectangles=rectangles
    partition=unary_union([box(*r.bounds) for r in rectangles])
    if abs(sum(r.area for r in rectangles)-partition.area) > 1e-7:
        diagnostics.error("equiv.region_overlap", "shared region partition overlaps")
        rectification.ok=False
    rectification.audit["partition_non_overlapping"] = abs(sum(r.area for r in rectangles)-partition.area) <= 1e-7
    rectification.audit["domains"]=audit
    rectification.audit["domain_region_ids"]=membership
    return rectification,membership


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def equivalence_from_document(
    document: BoardDocument | Mapping[str, Any],
    options: EquivalenceOptions,
    *,
    diagnostics: Optional[Diagnostics] = None,
    cell_sink: Optional[Callable[[EquivalentCell], None]] = None,
    mask_sink: Optional[Callable[[str, LayerMask, tuple[float, float]], None]] = None,
) -> EquivalenceResult:
    """Equivalent every copper and dielectric layer of *document*.

    Without *cell_sink* the cells are returned on the result.  With one, each
    cell is handed to the callback as it is produced and the result carries no
    cells: a board-wide grid at high ``px``/``py`` can hold millions of them, and
    the caller writing a CSV only ever needs one at a time.

    *mask_sink* receives ``(layer name, LayerMask, pixel_step_mm)`` once per
    equivalented layer, holding exactly the black/white raster that layer's cells
    were computed from plus the physical size of one raster pixel along X and Y
    (a picture that keeps the board's proportions needs both).  The callback owns
    the mask from then on, so a caller that writes an image can drop it
    immediately and never keep 43 of them resident.
    """
    diags = diagnostics if diagnostics is not None else Diagnostics()
    try:
        document = validate_document(document).internal_document()
    except ValidationError as exc:
        raise EquivalenceError(f"invalid intermediate JSON: {exc}") from exc

    step = _single_step(document)
    step_name = step.get("name")
    profile_id = step.get("profile_domain_id")
    domains = _domain_index(step)
    layers = _layer_index(step)

    # --- board outline ------------------------------------------------------
    px, py = options.grid.px, options.grid.py
    rectification, membership = plan_geometry(step, options, diags)
    if not rectification.rectangles:
        return EquivalenceResult(
            cells=[], layers=[], grid=build_grid([], options.grid,
                                                 diagnostics=diags),
            rectification=rectification, raster_px=options.grid.px,
            raster_py=options.grid.py, pixel_step_mm=(0.0, 0.0),
            board_bounds_mm=None, diagnostics=diags, fatal=True)

    bounds = _region_from_polygons(domains[profile_id].get("polygons")).bounds
    assert bounds is not None
    x_min, y_min, x_max, y_max = bounds
    from .mesh import resolve_mesh, resolve_raster
    mesh_options,mesh_config=resolve_mesh(step,rectification.rectangles,options.grid)
    grid = build_grid(rectification.rectangles,mesh_options,diagnostics=diags)
    if grid.cells:
        px,py=resolve_raster(bounds,grid,mesh_options,mesh_config)
    grid.mesh_configuration=mesh_config
    dx = (x_max - x_min) / px
    dy = (y_max - y_min) / py
    x_axis = RasterAxis(x_min, dx, px)
    y_axis = RasterAxis(y_min, dy, py)

    if not grid.cells:
        return EquivalenceResult(
            cells=[], layers=[], grid=grid, rectification=rectification,
            raster_px=px, raster_py=py, pixel_step_mm=(dx, dy),
            board_bounds_mm=(x_min, y_min, x_max, y_max),
            diagnostics=diags, fatal=True)

    # --- layers -------------------------------------------------------------
    needs_drills = any(l.get("type") == DIELECTRIC_TYPE and l.get("context", "BOARD") == "BOARD"
                       and l.get("geometry_status", "ready") == "ready" for l in step.get("layers", []))
    drills, drill_fatal = _collect_drills(step, diags) if needs_drills else ([], False)
    # A rectangularisation that lost an island or filled a hole produced a board
    # outline that is not the outline being equivalented; the run must not write
    # a plausible-looking CSV from it.
    fatal = drill_fatal or not rectification.ok
    reports: list[LayerReport] = []
    cells: list[EquivalentCell] = []
    emit = cells.append if cell_sink is None else cell_sink
    materials_by_layer: dict[str, dict] = {
        "__defaults__": options.materials.defaults_dict()
    }
    layer_materials: dict[str, ResolvedMaterials] = {}
    cell_without_pixel = 0
    step_domain = _region_from_polygons(domains[profile_id].get("polygons"))
    domain_regions = {}
    domain_sampling = {}

    for row in step.get("layers") or ():
        name = row.get("name", "?")
        type_name = row.get("type", "UNKNOWN")
        report = LayerReport(name=name, type=type_name, status="skipped")
        extent = row.get("z_mm")

        if row.get("context", "BOARD") != "BOARD":
            report.reason = "non-BOARD context has no physical slab"
            reports.append(report)
            diags.warn(EQUIV_LAYER_SKIPPED, f"layer {name!r} skipped: {report.reason}")
            continue

        if type_name not in COPPER_TYPES and type_name != DIELECTRIC_TYPE:
            report.reason = SKIP_REASONS.get(
                type_name,
                f"layer type {type_name} is not equivalented in v1; only copper "
                f"(SIGNAL/POWER_GROUND/MIXED) and DIELECTRIC are")
            reports.append(report)
            diags.warn(EQUIV_LAYER_SKIPPED,
                       f"layer {name!r} ({type_name}) skipped: {report.reason}")
            continue

        layer = layers.get(name)
        if layer is None or layer.get("geometry_status", "ready") != "ready":
            report.status = "failed"
            report.reason = row.get("geometry_error") or f"geometry is {row.get('geometry_status', 'missing')}"
            reports.append(report)
            diags.error(EQUIV_LAYER_FAILED,
                        f"equivalence candidate {name!r} ({type_name}) cannot be "
                        f"equivalented: {report.reason}")
            continue

        if not extent or extent.get("start_mm") is None:
            report.status = "failed"
            report.reason = "no absolute Z interval"
            reports.append(report)
            diags.error(EQUIV_LAYER_Z_UNKNOWN,
                        f"layer {name!r} has no absolute Z interval; its "
                        f"equivalence cell cannot be placed and drills cannot be "
                        f"matched")
            continue

        domain_id = layer.get("domain_id")
        domain_doc = domains.get(domain_id) if domain_id else None
        if domain_doc is None:
            report.status = "failed"
            report.reason = (layer.get("geometry_error")
                             or "no computational domain")
            reports.append(report)
            diags.error(EQUIV_MISSING_DOMAIN,
                        f"layer {name!r} has no usable computational domain: "
                        f"{report.reason}; AGENTS constraint 7 forbids "
                        f"substituting its copper polygons")
            continue

        if domain_id not in domain_regions:
            domain_regions[domain_id] = _region_from_polygons(domain_doc.get("polygons"))
        domain = domain_regions[domain_id]
        if not domain:
            report.status = "failed"
            report.reason = "computational domain is empty"
            reports.append(report)
            diags.error(EQUIV_LAYER_EMPTY,
                        f"layer {name!r} computational domain is empty; nothing "
                        f"to equivalent")
            continue

        z_start = float(extent["start_mm"])
        z_end = float(extent["end_mm"])
        thickness_known = bool(extent.get("thickness_known", False))
        report.domain_id = domain_id
        report.domain_source = domain_doc.get("source")
        report.z_start_mm = z_start
        report.z_end_mm = z_end
        report.thickness_known = thickness_known
        report.z_provenance = {k:v for k,v in extent.items() if k not in ("start_mm","end_mm","thickness_mm","thickness_known")}

        materials = layer_materials.get(name)
        if materials is None:
            materials = options.materials.resolve(name, "dielectric" if type_name == DIELECTRIC_TYPE else "copper")
            layer_materials[name] = materials
            materials_by_layer[name] = materials.as_dict()

        # Material 1 (black) is the copper pattern for a copper layer; for a
        # dielectric it starts empty. Drill scanline spans apply only to dielectric.
        pattern = (_region_from_polygons(layer.get("polygons"))
                   if type_name in COPPER_TYPES else Region())
        # Match drills by copper-plane topology first.  When a layer has no
        # stated thickness several layers can share a best-effort Z, and a
        # Z-only match would project the drill into a layer it never reaches.
        # Derived absolute Z is a fallback only for a dielectric without its
        # own span; unknown dielectric thickness emits a warning below.
        covering: list[_Drill] = []
        z_fallback = False
        z_mismatch = False
        for drill in drills if type_name == DIELECTRIC_TYPE else ():
            topological = _topological_overlap(layer, drill)
            by_z = _overlaps(z_start, z_end, drill.z_start_mm, drill.z_end_mm)
            if topological is None:
                matched = by_z
                z_fallback = True
            else:
                matched = topological
                if not thickness_known and topological != by_z:
                    z_mismatch = True
            if matched:
                covering.append(drill)
        if z_fallback and not thickness_known:
            diags.warn(
                EQUIV_DRILL_Z_FALLBACK,
                f"dielectric layer {name!r}: its thickness is unknown and it "
                f"has no copper-plane span, so drill coverage was decided "
                f"by best-effort absolute Z and may be wrong",
            )
        if z_mismatch:
            diags.warn(
                EQUIV_DRILL_Z_MISMATCH,
                f"layer {name!r}: a drill's copper-plane span and its "
                f"best-effort absolute Z disagree because the layer thickness "
                f"is unknown; the topological span was used",
            )
        report.drill_overlays = [d.name for d in covering]
        black = [pattern] + [d.region for d in covering]
        if domain_id not in domain_sampling:
            members = set(membership.get(domain_id, []))
            domain_grid = replace(grid, cells=[c for c in grid.cells if c.region_id in members])
            domain_sampling[domain_id] = (domain_grid, _sampling_plan(domain_grid, x_axis, y_axis))
        domain_grid, sampling_plan = domain_sampling[domain_id]
        mask = LayerMask(px, py) if mask_sink is not None else None
        cell_count, partial, no_pixel = _evaluate_layer(
            name, type_name, domain, black,
            z_start, z_end, thickness_known, materials,
            domain_grid,
            x_axis, y_axis, diags, emit,
            mask=mask,
            step_domain=step_domain,
            sampling_plan=sampling_plan,
        )
        report.status = "equivalent"
        report.cell_count = cell_count
        report.partial_cells = partial
        cell_without_pixel += no_pixel
        reports.append(report)
        if mask is not None:
            # Hand the raster over as soon as the layer is done; the sink writes
            # it out and drops it, so only one layer mask is ever resident.
            mask_sink(name, mask, (dx, dy))

    if cell_without_pixel or any(r.status == "failed" for r in reports):
        fatal = True

    return EquivalenceResult(
        cells=cells,
        layers=reports,
        grid=grid,
        rectification=rectification,
        raster_px=px,
        raster_py=py,
        pixel_step_mm=(dx, dy),
        board_bounds_mm=(x_min, y_min, x_max, y_max),
        diagnostics=diags,
        materials_by_layer=materials_by_layer,
        cell_without_pixel=cell_without_pixel,
        fatal=fatal,
    )


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write_materialmap(result: EquivalenceResult, path: str, *, step_name=None) -> list[str]:
    """Atomically write one region-based JSON; no CSV/metadata sidecar."""
    import os
    from .materialmap import MaterialMapWriter
    temporary = str(path) + ".tmp"
    try:
        with MaterialMapWriter(temporary, step_name=step_name) as writer:
            for cell in result.cells:
                writer.add(cell)
            writer.finish(result)
        os.replace(temporary, path)
    except Exception:
        if os.path.isfile(temporary):
            os.remove(temporary)
        raise
    return [str(path)]


def write_run_report(result: EquivalenceResult, path: str, *, source_path=None, step_name=None) -> str:
    """Separate execution/geometry audit; no per-cell indexes or sampling statistics."""
    import json
    from pathlib import Path
    data = result.metadata(source_path=source_path, step_name=step_name)
    for key in ("schema", "generator", "coverage"):
        data.pop(key, None)
    data["grid"].pop("cells", None)
    for layer in data["layers"]:
        layer.pop("partial_domain_cells", None)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1)+"\n", encoding="utf-8")
    return str(path)
