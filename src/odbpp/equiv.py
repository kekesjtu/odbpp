"""PCB material equivalence: preview JSON in, per-cell thermal properties out.

This is data-flow step 4 of the AGENTS plan.  It consumes the ``odbpp/preview-2``
intermediate document -- the only interface it is allowed to rely on -- and
produces one equivalent cell per (physical layer, board grid cell) with the
anisotropic conductivity, density and specific heat from the derivation docs.

Model, in one paragraph
-----------------------
Each physical layer is two phases: material 1 is copper (``black``), material 2
is the base laminate (``white``), and both are assumed to run through the whole
layer thickness (AGENTS 公式推导, assumption 3).  Every layer's pattern is
rasterised at the shared :class:`~odbpp.grid.BoardGrid`; within a cell the
per-pixel black mask gives

* ``kx`` -- series along each pixel row, parallel across rows;
* ``ky`` -- series along each pixel column, parallel across columns;
* ``kz`` -- parallel across pixel columns: ``phi*k1 + (1-phi)*k2``;
* ``density`` -- volume-fraction weighted: ``phi*d1 + (1-phi)*d2``;
* ``cp`` -- mass weighted: ``(phi*d1*c1 + (1-phi)*d2*c2) / density``.

Drills (AGENTS 等效器输入与材料规则 6) are a cross-layer pattern, not a layer:
every drill whose Z interval overlaps the layer is projected into it and made
**solid copper (black)** for both copper and dielectric layers.  A drill whose Z
span cannot be resolved is an error, never a through-hole guess.

The computational domain decides whether a layer *exists* at a cell (AGENTS
constraint 8).  Pixels outside the domain are never counted as base laminate --
board-outside is not FR4.  A cell that only partially overlaps the domain is
evaluated from the pixels that are inside it, and the fact is recorded so a
reader can see it rather than infer it.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence

from .clipper import Region, Ring, difference, intersection, union
from .diagnostics import (
    EQUIV_DIELECTRIC_FEATURES,
    EQUIV_DRILL_SPAN_UNKNOWN,
    EQUIV_DRILL_Z_FALLBACK,
    EQUIV_DRILL_Z_MISMATCH,
    EQUIV_LAYER_EMPTY,
    EQUIV_LAYER_FAILED,
    EQUIV_LAYER_SKIPPED,
    EQUIV_LAYER_Z_UNKNOWN,
    EQUIV_MISSING_DOMAIN,
    EQUIV_PARTIAL_DOMAIN,
    GRID_CELL_WITHOUT_PIXEL,
    Diagnostics,
)
from .errors import OdbError
from .export import KNOWN_SCHEMAS
from .grid import BoardGrid, GridOptions, build_grid
from .materials import MaterialBook, ResolvedMaterials
from .raster import LayerMask, RasterAxis, region_edges
from .rectify import RectifyOptions, RectifyResult, rectify_region

__all__ = [
    "METADATA_SCHEMA",
    "CSV_COLUMNS",
    "EquivalenceOptions",
    "EquivalentCell",
    "LayerReport",
    "EquivalenceResult",
    "equivalence_from_document",
    "write_materialmap",
    "write_metadata",
]

#: Schema of the sidecar metadata document.  Bumped when its layout changes.
#: ``materialmap-2`` adds the per-cell domain coverage columns and states their
#: downstream meaning; a reader must not assume a ``materialmap-1`` layout.
METADATA_SCHEMA = "odbpp/materialmap-2"

#: The written CSV columns.  Deliberately self-describing: every quantity carries
#: its unit in the header, because the file is meant to be readable without the
#: sidecar and the sidecar is meant to be sufficient for anything the CSV cannot
#: express.  The layout is our own; the FloTHERM reference was read for *what*
#: needs expressing, not copied.
CSV_COLUMNS = [
    "layer",
    "layer_type",
    "region_id",
    "cell_i",
    "cell_j",
    "cell_x0_mm",
    "cell_y0_mm",
    "cell_x1_mm",
    "cell_y1_mm",
    "z_start_mm",
    "z_end_mm",
    "z_thickness_known",
    "copper_area_fraction",
    "kx_w_mk",
    "ky_w_mk",
    "kz_w_mk",
    "density_kg_m3",
    "specific_heat_j_kgk",
    "pixel_rows",
    "pixel_cols",
    "pixel_count",
    "cell_area_mm2",
    "domain_area_mm2",
    "domain_coverage",
    "partial_domain",
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


class EquivalenceError(OdbError):
    """The input document cannot be equivalented at all."""


@dataclass(frozen=True)
class EquivalenceOptions:
    grid: GridOptions
    materials: MaterialBook = field(default_factory=MaterialBook)
    rectify: RectifyOptions = field(default_factory=RectifyOptions)
    #: Emit a warning for partially covered cells (on by default; a run that
    #: silently drops board-outside area is worse than one noisy line).
    warn_partial_domains: bool = True


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

    def csv_row(self) -> list:
        return [
            self.layer_name, self.layer_type, self.region_id, self.i, self.j,
            _r(self.x0), _r(self.y0), _r(self.x1), _r(self.y1),
            _r(self.z_start_mm, 6), _r(self.z_end_mm, 6),
            int(self.thickness_known),
            _r(self.copper_fraction, 6), _r(self.kx, 6), _r(self.ky, 6),
            _r(self.kz, 6), _r(self.density, 4), _r(self.specific_heat, 4),
            self.pixel_rows, self.pixel_cols, self.pixel_count,
            _r(self.cell_area_mm2, 6), _r(self.domain_area_mm2, 6),
            _r(self.domain_coverage, 6), int(self.partial_domain),
        ]

    def as_dict(self) -> dict:
        return dict(zip(CSV_COLUMNS, self.csv_row()))


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
                 source_schema: Optional[str] = None,
                 step_name: Optional[str] = None) -> dict:
        from . import __version__

        return {
            "schema": METADATA_SCHEMA,
            "generator": {"name": "odbpp", "version": __version__},
            "source": {
                "path": source_path,
                "preview_schema": source_schema,
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
            "coverage": {
                "columns": ["cell_area_mm2", "domain_area_mm2",
                            "domain_coverage"],
                "semantics": "cell_x0/y0/x1/y1 is the shared board-grid cell; "
                             "the reported kx/ky/kz/density/specific_heat apply to "
                             "the fraction domain_coverage of that cell which the "
                             "layer's computational domain covers. The uncovered "
                             "remainder is board-outside/void, not FR4: scale "
                             "cell mass and heat capacity by domain_coverage and "
                             "do not add a material there",
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


def _series(phi: float, k1: float, k2: float) -> float:
    """Series conductivity of a row/column with black fraction *phi*."""
    return 1.0 / (phi / k1 + (1.0 - phi) / k2)


def _region_from_polygons(polygons: Sequence[Mapping[str, Any]]) -> Region:
    rings: list[tuple[Sequence[tuple[float, float]], bool]] = []
    for polygon in polygons or ():
        points = [(float(x), float(y)) for x, y in polygon.get("points", ())]
        rings.append((points, not bool(polygon.get("island", True))))
    return Region([r for r in (Ring(points, is_hole) for points, is_hole in rings)
                   if len(r) >= 3])


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
    #: Half-open copper-plane interval ``(start, end)`` when the document
    #: resolved ``START_NAME``/``END_NAME``; ``None`` when it did not, in which
    #: case only absolute Z can be used (and only safely with a known thickness).
    copper_span: Optional[tuple[int, int]] = None


def _copper_span(row: Mapping[str, Any]) -> Optional[tuple[int, int]]:
    """Read ``z_span.copper_index`` from a stackup row, if it is well formed."""
    span = row.get("z_span")
    index = span.get("copper_index") if isinstance(span, Mapping) else None
    if isinstance(index, (list, tuple)) and len(index) == 2:
        try:
            return (int(index[0]), int(index[1]))
        except (TypeError, ValueError):
            return None
    return None


def _collect_drills(step: Mapping[str, Any],
                    diagnostics: Diagnostics) -> tuple[list[_Drill], bool]:
    """Every drill layer with a resolvable Z span, plus whether any was not."""
    layers = _layer_index(step)
    drills: list[_Drill] = []
    fatal = False
    for row in step.get("stackup") or ():
        if row.get("type") != "DRILL":
            continue
        name = row.get("name", "?")
        extent = row.get("z_mm")
        if not extent or extent.get("start_mm") is None:
            diagnostics.error(
                EQUIV_DRILL_SPAN_UNKNOWN,
                f"drill layer {name!r} has no resolvable Z interval "
                f"(START_NAME/END_NAME do not resolve to copper layers); which "
                f"layers it crosses cannot be determined, so its solid-copper "
                f"projection is unknown. Refusing to guess a through-hole.",
            )
            fatal = True
            continue
        layer = layers.get(name)
        if layer is None:
            diagnostics.error(
                EQUIV_DRILL_SPAN_UNKNOWN,
                f"drill layer {name!r} is in the stack but has no geometry in "
                f"the preview; its projection cannot be computed",
            )
            fatal = True
            continue
        drills.append(_Drill(name=name,
                             z_start_mm=float(extent["start_mm"]),
                             z_end_mm=float(extent["end_mm"]),
                             region=_region_from_polygons(layer.get("polygons")),
                             copper_span=_copper_span(row)))
    return drills, fatal


def _topological_overlap(layer_type: str, layer_doc: Mapping[str, Any],
                         drill: _Drill) -> Optional[bool]:
    """Decide drill/layer overlap from copper-plane indices, or ``None``.

    Absolute Z is a derived quantity: when a stack layer has no stated
    thickness, several layers can share a best-effort ``start_mm == end_mm``,
    and matching by Z would then mark a layer the drill never reaches.  The
    copper-plane index is the topology the format authored, so it wins whenever
    both sides expose one.  A copper layer is a plane, so it is covered
    inclusively; a dielectric occupies the half-open interval between two planes.
    """
    span = drill.copper_span
    if span is None:
        return None
    start, end = span
    if layer_type in COPPER_TYPES:
        index = layer_doc.get("copper_index")
        if index is None:
            return None
        return start <= int(index) <= end
    layer_span = layer_doc.get("z_span")
    index = (layer_span.get("copper_index")
             if isinstance(layer_span, Mapping) else None)
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
        self._edges = edges
        self._order = sorted(
            range(len(edges)),
            key=lambda i: min(edges[i][1], edges[i][3]),
        )
        self._cursor = 0
        self._active: list[int] = []

    def spans_at(self, y: float) -> list[tuple[float, float]]:
        edges = self._edges
        order = self._order
        while self._cursor < len(order):
            index = order[self._cursor]
            if min(edges[index][1], edges[index][3]) <= y:
                self._active.append(index)
                self._cursor += 1
            else:
                break
        active = [i for i in self._active
                  if max(edges[i][1], edges[i][3]) > y]
        self._active = active
        crossings: list[float] = []
        for index in active:
            x1, y1, x2, y2 = edges[index]
            if (y1 <= y) == (y2 <= y):
                continue
            t = (y - y1) / (y2 - y1)
            crossings.append(x1 + (x2 - x1) * t)
        if len(crossings) < 2:
            return []
        crossings.sort()
        spans: list[tuple[float, float]] = []
        for k in range(0, len(crossings) - 1, 2):
            left, right = crossings[k], crossings[k + 1]
            if right > left:
                spans.append((left, right))
        return spans


def _evaluate_layer(
    name: str,
    type_name: str,
    domain: Region,
    black: Region,
    drill_names: list[str],
    z_start: float,
    z_end: float,
    thickness_known: bool,
    materials: ResolvedMaterials,
    grid: BoardGrid,
    x_axis: RasterAxis,
    y_axis: RasterAxis,
    diagnostics: Diagnostics,
    emit: Callable[[EquivalentCell], None],
    *,
    warn_partial: bool,
    mask: Optional[LayerMask] = None,
) -> tuple[int, int, int]:
    """Emit one layer's cells; return ``(count, partial_count, cell_without_pixel)``."""
    k1, k2 = materials.k1, materials.k2
    d1, c1 = materials.black.density_kg_m3, materials.black.specific_heat_j_kgk
    d2, c2 = materials.white.density_kg_m3, materials.white.specific_heat_j_kgk

    domain_scan = _Scanline(region_edges(domain))
    black_scan = _Scanline(region_edges(black))
    width = x_axis.count

    emitted = 0
    partial_cells = 0
    no_pixel = 0
    min_coverage = 1.0

    for band in sorted(grid.cells_by_band):
        band_cells = grid.cells_by_band[band]
        b0, b1 = y_axis.pixel_index(grid.y_lines[band]), \
            y_axis.pixel_index(grid.y_lines[band + 1])

        ranges: dict[int, tuple[int, int]] = {}
        for cell in band_cells:
            a0, a1 = x_axis.pixel_index(cell.rect.x0), \
                x_axis.pixel_index(cell.rect.x1)
            ranges[cell.i] = (a0, a1)
            if a1 <= a0 or b1 <= b0:
                no_pixel += 1
                diagnostics.error(
                    GRID_CELL_WITHOUT_PIXEL,
                    f"layer {name!r}: grid cell (i={cell.i}, j={cell.j}) at "
                    f"x=[{cell.rect.x0:g},{cell.rect.x1:g}], "
                    f"y=[{cell.rect.y0:g},{cell.rect.y1:g}] mm contains no "
                    f"raster pixel centre; increase px/py or reduce nx/ny",
                )
                continue


        # Per-band accumulators.  ``diff`` arrays let column counts be built
        # with a prefix sum instead of a Python loop over the full width.
        dom_diff = [0] * (width + 1)
        black_diff = [0] * (width + 1)
        stats: dict[int, list[float]] = {
            cell.i: [0.0, 0.0, 0, 0.0] for cell in band_cells
        }

        for b in range(b0, b1):
            y = y_axis.centre(b)
            dom_row = bytearray(width)
            black_row = bytearray(width)
            dom_spans = domain_scan.spans_at(y)
            black_spans = black_scan.spans_at(y)
            for span in dom_spans:
                a0, a1 = x_axis.pixel_index(span[0]), x_axis.pixel_index(span[1])
                if a1 > a0:
                    dom_row[a0:a1] = b"\x01" * (a1 - a0)
                    dom_diff[a0] += 1
                    dom_diff[a1] -= 1
            for span in black_spans:
                a0, a1 = x_axis.pixel_index(span[0]), x_axis.pixel_index(span[1])
                if a1 > a0:
                    black_row[a0:a1] = b"\x01" * (a1 - a0)
                    black_diff[a0] += 1
                    black_diff[a1] -= 1
            if mask is not None:
                mask.set_spans(b, dom_spans, black_spans, x_axis)

            for cell in band_cells:
                index = ranges.get(cell.i)
                if index is None:
                    continue
                a0, a1 = index
                if a0 >= a1:
                    continue
                domain_pixels = sum(dom_row[a0:a1])
                if domain_pixels == 0:
                    continue
                black_pixels = sum(black_row[a0:a1])
                stat = stats[cell.i]
                stat[0] += black_pixels
                stat[1] += domain_pixels
                stat[2] += 1
                stat[3] += _series(black_pixels / domain_pixels, k1, k2)

        run = 0
        col_domain = [0] * width
        for a in range(width):
            run += dom_diff[a]
            col_domain[a] = run
        run = 0
        col_black = [0] * width
        for a in range(width):
            run += black_diff[a]
            col_black[a] = run

        for cell in band_cells:
            index = ranges.get(cell.i)
            if index is None:
                continue
            a0, a1 = index
            stat = stats[cell.i]
            if stat[1] <= 0.0:
                continue
            column_sum = 0.0
            columns = 0
            for a in range(a0, a1):
                count = col_domain[a]
                if count <= 0:
                    continue
                column_sum += _series(col_black[a] / count, k1, k2)
                columns += 1
            rows = int(stat[2])
            phi = stat[0] / stat[1]
            kx = stat[3] / rows if rows else k1
            ky = column_sum / columns if columns else k2
            kz = phi * k1 + (1.0 - phi) * k2
            density = phi * d1 + (1.0 - phi) * d2
            specific_heat = (phi * d1 * c1 + (1.0 - phi) * d2 * c2) / density
            full = float((a1 - a0) * (b1 - b0))
            cell_area = (cell.rect.x1 - cell.rect.x0) * (cell.rect.y1 - cell.rect.y0)
            coverage = (stat[1] / full) if full else 0.0
            coverage = min(1.0, max(0.0, coverage))
            partial = coverage < 1.0 - 1e-9
            if partial:
                partial_cells += 1
                min_coverage = min(min_coverage, coverage)
            emit(EquivalentCell(
                layer_name=name, layer_type=type_name, region_id=cell.region_id,
                i=cell.i, j=cell.j,
                x0=cell.rect.x0, y0=cell.rect.y0,
                x1=cell.rect.x1, y1=cell.rect.y1,
                z_start_mm=z_start, z_end_mm=z_end,
                thickness_known=thickness_known,
                copper_fraction=phi, kx=kx, ky=ky, kz=kz,
                density=density, specific_heat=specific_heat,
                pixel_rows=rows, pixel_cols=columns,
                pixel_count=int(stat[1]), cell_area_mm2=cell_area,
                domain_area_mm2=coverage * cell_area,
                domain_coverage=coverage, partial_domain=partial,
            ))
            emitted += 1

    if partial_cells and warn_partial:
        diagnostics.warn(
            EQUIV_PARTIAL_DOMAIN,
            f"layer {name!r}: {partial_cells} of {emitted} cell(s) are only "
            f"partially covered by the layer's computational domain (minimum "
            f"coverage {min_coverage:.4f}); only in-domain pixel centres were "
            f"used, the cell rectangle is the shared grid cell, and the reported "
            f"properties apply to the covered fraction only (see "
            f"domain_coverage)",
        )

    return emitted, partial_cells, no_pixel


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def equivalence_from_document(
    document: Mapping[str, Any],
    options: EquivalenceOptions,
    *,
    diagnostics: Optional[Diagnostics] = None,
    cell_sink: Optional[Callable[[EquivalentCell], None]] = None,
    mask_sink: Optional[Callable[[str, LayerMask], None]] = None,
) -> EquivalenceResult:
    """Equivalent every copper and dielectric layer of *document*.

    Without *cell_sink* the cells are returned on the result.  With one, each
    cell is handed to the callback as it is produced and the result carries no
    cells: a board-wide grid at high ``px``/``py`` can hold millions of them, and
    the caller writing a CSV only ever needs one at a time.

    *mask_sink* receives ``(layer name, LayerMask)`` once per equivalented layer,
    holding exactly the black/white raster that layer's cells were computed from.
    The callback owns the mask from then on, so a caller that writes an image can
    drop it immediately and never keep 43 of them resident.
    """
    diags = diagnostics if diagnostics is not None else Diagnostics()
    schema = document.get("schema")
    if schema not in KNOWN_SCHEMAS:
        raise EquivalenceError(
            f"unsupported preview schema {schema!r}; expected one of "
            f"{', '.join(KNOWN_SCHEMAS)}. Regenerate the intermediate JSON "
            f"with this version of odbpp.")
    if schema == "odbpp/preview-1":
        raise EquivalenceError(
            "schema odbpp/preview-1 has no per-layer computational domain; the "
            "material equivalence cannot tell where a layer exists and must not "
            "infer it from copper polygons. Regenerate with odbpp/preview-2.")

    step = _single_step(document)
    step_name = step.get("name")
    domains = _domain_index(step)
    layers = _layer_index(step)

    # --- board outline ------------------------------------------------------
    step_domain = domains.get("step_profile")
    if step_domain is None:
        raise EquivalenceError(
            f"step {step_name!r} has no step_profile domain; the board outline "
            f"is required to size the raster and rectangularise the board")
    profile = _region_from_polygons(step_domain.get("polygons"))
    px, py = options.grid.px, options.grid.py
    profile_bounds = profile.bounds
    rectify_options = options.rectify
    if rectify_options.tolerance_mm is None and profile_bounds is not None:
        # Auto: the staircase step is twice the pixel pitch, the coarsest value
        # that still leaves every forced interval a raster sample.
        pitch_x = (profile_bounds[2] - profile_bounds[0]) / px
        pitch_y = (profile_bounds[3] - profile_bounds[1]) / py
        rectify_options = replace(rectify_options,
                                  tolerance_mm=2.0 * max(pitch_x, pitch_y))
    rectification = rectify_region(profile, rectify_options, diagnostics=diags)
    if not rectification.rectangles:
        return EquivalenceResult(
            cells=[], layers=[], grid=build_grid([], options.grid,
                                                 diagnostics=diags),
            rectification=rectification, raster_px=options.grid.px,
            raster_py=options.grid.py, pixel_step_mm=(0.0, 0.0),
            board_bounds_mm=None, diagnostics=diags, fatal=True)

    bounds = rectification.bounds
    assert bounds is not None
    x_min, y_min, x_max, y_max = bounds
    dx = (x_max - x_min) / px
    dy = (y_max - y_min) / py
    x_axis = RasterAxis(x_min, dx, px)
    y_axis = RasterAxis(y_min, dy, py)

    grid = build_grid(
        rectification.rectangles, options.grid, diagnostics=diags,
        capacity_x=lambda a, b: x_axis.pixel_index(b) - x_axis.pixel_index(a),
        capacity_y=lambda a, b: y_axis.pixel_index(b) - y_axis.pixel_index(a),
    )
    if not grid.cells:
        return EquivalenceResult(
            cells=[], layers=[], grid=grid, rectification=rectification,
            raster_px=px, raster_py=py, pixel_step_mm=(dx, dy),
            board_bounds_mm=(x_min, y_min, x_max, y_max),
            diagnostics=diags, fatal=True)

    # --- layers -------------------------------------------------------------
    drills, drill_fatal = _collect_drills(step, diags)
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

    for row in step.get("stackup") or ():
        name = row.get("name", "?")
        type_name = row.get("type", "UNKNOWN")
        report = LayerReport(name=name, type=type_name, status="skipped")
        extent = row.get("z_mm")

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
        if layer is None:
            report.status = "failed"
            report.reason = ("layer is in the stack but has no geometry in the "
                             "preview; its profile probably could not be parsed")
            reports.append(report)
            diags.error(EQUIV_LAYER_FAILED,
                        f"equivalence candidate {name!r} ({type_name}) is absent "
                        f"from the preview layers, so it cannot be equivalented")
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
            report.reason = (layer.get("domain_error")
                             or "no computational domain")
            reports.append(report)
            diags.error(EQUIV_MISSING_DOMAIN,
                        f"layer {name!r} has no usable computational domain: "
                        f"{report.reason}; AGENTS constraint 7 forbids "
                        f"substituting its copper polygons")
            continue

        domain = _region_from_polygons(domain_doc.get("polygons"))
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

        materials = layer_materials.get(name)
        if materials is None:
            materials = options.materials.resolve(name)
            layer_materials[name] = materials
            materials_by_layer[name] = materials.as_dict()

        # Material 1 (black) is the copper pattern for a copper layer; for a
        # dielectric it starts empty.  Drills are then unioned in for both.
        pattern = (_region_from_polygons(layer.get("polygons"))
                   if type_name in COPPER_TYPES else Region())
        # Match drills by copper-plane topology first.  When a layer has no
        # stated thickness several layers can share a best-effort Z, and a
        # Z-only match would project the drill into a layer it never reaches.
        # Absolute Z is only a fallback for documents without a span, and it is
        # safe only when the thickness is known.
        covering: list[_Drill] = []
        z_fallback = False
        z_mismatch = False
        for drill in drills:
            topological = _topological_overlap(type_name, layer, drill)
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
                f"layer {name!r}: its thickness is unknown and at least one "
                f"drill has no copper-plane span, so drill coverage was decided "
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
        black = pattern
        if covering:
            overlay = union(*(d.region for d in covering))
            black = union(black, overlay)
        black = intersection(black, domain) if black else Region()

        # A dielectric's own geometry is its material (white).  In v1 the black
        # phase is the drill projection, so if the authored material area does
        # not match ``domain - drills`` the layer carries features this version
        # does not model.  Say so rather than silently treating a void as FR4.
        if type_name == DIELECTRIC_TYPE:
            material = _region_from_polygons(layer.get("polygons"))
            expected = difference(domain, black)
            scale = max(material.area, expected.area, 1e-9)
            if abs(material.area - expected.area) > 0.01 * scale:
                diags.warn(
                    EQUIV_DIELECTRIC_FEATURES,
                    f"dielectric layer {name!r}: authored material area "
                    f"{material.area:.4f} mm2 differs from domain-minus-drills "
                    f"{expected.area:.4f} mm2; features other than drills are "
                    f"ignored in v1",
                )

        mask = LayerMask(px, py) if mask_sink is not None else None
        cell_count, partial, no_pixel = _evaluate_layer(
            name, type_name, domain, black, report.drill_overlays,
            z_start, z_end, thickness_known, materials,
            grid, x_axis, y_axis, diags, emit,
            warn_partial=options.warn_partial_domains,
            mask=mask,
        )
        report.status = "equivalent"
        report.cell_count = cell_count
        report.partial_cells = partial
        cell_without_pixel += no_pixel
        reports.append(report)
        if mask is not None:
            # Hand the raster over as soon as the layer is done; the sink writes
            # it out and drops it, so only one layer mask is ever resident.
            mask_sink(name, mask)

    if cell_without_pixel:
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

def write_materialmap(
    result: EquivalenceResult,
    csv_path: str,
    metadata_path: Optional[str] = None,
    *,
    source_path: Optional[str] = None,
    source_schema: Optional[str] = None,
    step_name: Optional[str] = None,
) -> list[str]:
    """Write the CSV and (optionally) the metadata sidecar; return the paths."""
    written: list[str] = [csv_path]
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for cell in result.cells:
            writer.writerow(cell.csv_row())
    if metadata_path:
        write_metadata(result, metadata_path, source_path=source_path,
                       source_schema=source_schema, step_name=step_name)
        written.append(metadata_path)
    return written


def write_metadata(
    result: EquivalenceResult,
    path: str,
    *,
    source_path: Optional[str] = None,
    source_schema: Optional[str] = None,
    step_name: Optional[str] = None,
) -> str:
    """Write the metadata sidecar and return its path.

    The CSV can carry numbers and identifiers; this document carries what it
    cannot: units, absolute-Z trust, where every material value came from, the
    rectangularisation error, and the diagnostics for the run.
    """
    import json

    document = result.metadata(source_path=source_path,
                               source_schema=source_schema,
                               step_name=step_name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1,
                  sort_keys=False)
        handle.write("\n")
    return path
