"""Region-based MaterialMap JSON; indexes and sampling state stay internal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from .grid import uniform_lines

PROPERTIES = ["kx", "ky", "kz", "density", "specific_heat"]
UNITS = {"length": "mm", "thermal_conductivity": "W/(m*K)",
         "density": "kg/m3", "specific_heat": "J/(kg*K)"}
Positive = Annotated[FiniteFloat, Field(gt=0)]
Pair = Annotated[tuple[FiniteFloat, FiniteFloat], Field(strict=False)]
ZPair = Annotated[tuple[FiniteFloat | None, FiniteFloat | None], Field(strict=False)]
Values = Annotated[tuple[Positive, Positive, Positive, Positive, Positive], Field(strict=False)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class LayerIdentity(_Model):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)


class Bounds(_Model):
    x: Pair
    y: Pair
    z: ZPair

    @model_validator(mode="after")
    def ordered(self):
        if self.x[0] >= self.x[1] or self.y[0] >= self.y[1]:
            raise ValueError("XY bounds must have positive extent")
        if any(v is not None and v < 0 for v in self.z):
            raise ValueError("Z bounds must be nonnegative or null")
        if None not in self.z and self.z[0] > self.z[1]:
            raise ValueError("Z bounds must increase")
        return self


class GridCounts(_Model):
    x: int = Field(ge=1, le=1_000_000)
    y: int = Field(ge=1, le=1_000_000)
    z: int = Field(ge=1, le=1)


class MaterialRegion(_Model):
    layer: LayerIdentity
    bounds_mm: Bounds
    grid_counts: GridCounts
    values: list[Values]

    @model_validator(mode="after")
    def rectangular(self):
        expected = self.grid_counts.x*self.grid_counts.y*self.grid_counts.z
        if len(self.values) != expected:
            raise ValueError(f"region requires exactly {expected} row-major property rows, got {len(self.values)}")
        return self

    def axis_lines(self, axis):
        return uniform_lines(*getattr(self.bounds_mm,axis),getattr(self.grid_counts,axis))


class MaterialMapDocument(_Model):
    units: dict[str, str]
    properties: list[str]
    drill_model: Literal["solid_copper"]
    drill_scope: Literal["dielectric_only"]
    step: str | None = None
    regions: list[MaterialRegion] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        if self.units != UNITS or self.properties != PROPERTIES:
            raise ValueError("MaterialMap units and five-property order must match the current contract")
        layers = {}
        for region in self.regions:
            prior = layers.setdefault(region.layer.name, [])
            for other in prior:
                if other.layer.type != region.layer.type or other.bounds_mm.z != region.bounds_mm.z:
                    raise ValueError(f"layer {region.layer.name!r}: inconsistent type or Z")
                if (max(other.bounds_mm.x[0], region.bounds_mm.x[0]) < min(other.bounds_mm.x[1], region.bounds_mm.x[1])
                        and max(other.bounds_mm.y[0], region.bounds_mm.y[0]) < min(other.bounds_mm.y[1], region.bounds_mm.y[1])):
                    raise ValueError(f"layer {region.layer.name!r}: overlapping regions")
            prior.append(region)
        return self


def read_document(path: str | Path) -> MaterialMapDocument:
    return MaterialMapDocument.model_validate_json(Path(path).read_bytes())


def iter_cells(document: MaterialMapDocument):
    """Reconstruct local uniform cells; X is fastest, then Y, with one Z cell."""
    for region in document.regions:
        nx = region.grid_counts.x
        xs, ys = region.axis_lines("x"), region.axis_lines("y")
        for q, values in enumerate(region.values):
            j, i = divmod(q, nx)
            yield region, (xs[i],ys[j],xs[i+1],ys[j+1]), values


class MaterialMapWriter:
    """Stream regions while retaining property records for at most one layer.

    The sampler emits interleaved Y bands. Buffering one layer permits canonical
    region / Y / X ordering without storing the whole board or changing sampling.
    finish() validates against the actual planned rectangles, axes and layer counts.
    A caller must replace its final file only after finish succeeds.
    """
    def __init__(self, path, *, step_name=None):
        self.handle = open(path, "w", encoding="utf-8", newline="\n")
        header = {"units": UNITS, "properties": PROPERTIES,
                  "drill_model": "solid_copper", "drill_scope": "dielectric_only"}
        if step_name is not None:
            header["step"] = step_name
        self.handle.write(json.dumps(header, ensure_ascii=False)[:-1] + ', "regions": [\n')
        self.layer = None
        self.closed_layers = set()
        self.pending = {}
        self.headers = []
        self.count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.handle.close()

    def add(self, cell):
        if self.layer != cell.layer_name:
            self._flush()
            if cell.layer_name in self.closed_layers:
                raise ValueError("MaterialMap sampling must finish each layer before starting another")
            self.layer = cell.layer_name
        group = self.pending.setdefault(cell.region_id, [])
        group.append(cell)
        self.count += 1

    def _flush(self):
        for rid, cells in sorted(self.pending.items()):
            first = cells[0]
            xs = sorted({v for c in cells for v in (c.x0, c.x1)})
            ys = sorted({v for c in cells for v in (c.y0, c.y1)})
            if xs != uniform_lines(xs[0],xs[-1],len(xs)-1) or ys != uniform_lines(ys[0],ys[-1],len(ys)-1):
                raise ValueError(f"layer {self.layer!r} region {rid}: local Cartesian cells must be uniform")
            ordered = sorted(cells, key=lambda c: (c.y0, c.x0))
            expected = [(a, b, c, d) for b, d in zip(ys, ys[1:]) for a, c in zip(xs, xs[1:])]
            if [(c.x0, c.y0, c.x1, c.y1) for c in ordered] != expected:
                raise ValueError(f"layer {self.layer!r} region {rid}: incomplete/duplicate Cartesian cells")
            if any((c.layer_type, c.z_start_mm, c.z_end_mm, c.thickness_known) !=
                   (first.layer_type, first.z_start_mm, first.z_end_mm, first.thickness_known) for c in cells):
                raise ValueError("inconsistent region layer/Z identity")
            z = [first.z_start_mm, first.z_end_mm] if first.thickness_known else [None, None]
            region = MaterialRegion.model_validate({
                "layer": {"name": first.layer_name, "type": first.layer_type},
                "bounds_mm": {"x": [xs[0], xs[-1]], "y": [ys[0], ys[-1]], "z": z},
                "grid_counts": {"x":len(xs)-1,"y":len(ys)-1,"z":1},
                "values": [c.property_values() for c in ordered]})
            if self.headers:
                self.handle.write(',\n')
            self.handle.write(region.model_dump_json())
            self.headers.append((first.layer_name, rid, xs, ys, len(cells)))
        if self.layer is not None:
            self.closed_layers.add(self.layer)
        self.pending.clear()

    def finish(self, result):
        if result.fatal:
            raise ValueError("cannot write an untrustworthy MaterialMap")
        self._flush()
        counts = {}
        for name, rid, xs, ys, count in self.headers:
            r = result.rectification.rectangles[rid]
            if (xs[0], ys[0], xs[-1], ys[-1]) != r.bounds:
                raise ValueError(f"layer {name!r} region {rid}: cell extent differs from planned region")
            local = result.grid.regions[rid]
            if xs != local.x_lines or ys != local.y_lines:
                raise ValueError("serialized region differs from its planned local Cartesian grid")
            counts[name] = counts.get(name, 0)+count
        expected = {r.name: r.cell_count for r in result.layers if r.status == "equivalent"}
        if not counts or counts != expected:
            raise ValueError("MaterialMap cell counts differ from the completed physical layers")
        self.handle.write('\n]}\n')
        self.handle.flush()


def restore_report_grid(report: dict, document: MaterialMapDocument) -> dict:
    """Restore diagnostic cells in memory; indexes are never written to the map/report."""
    from copy import deepcopy
    out = deepcopy(report)
    grid = out["grid"]
    rects = out["rectification"]["rectangles"]
    ids = {(r["x0_mm"], r["y0_mm"], r["x1_mm"], r["y1_mm"]): n for n, r in enumerate(rects)}
    cells = {}
    planned = {r["region_id"]:r for r in grid["regions"]}
    for region in document.regions:
        xy = (region.bounds_mm.x[0],region.bounds_mm.y[0],region.bounds_mm.x[1],region.bounds_mm.y[1])
        rid = ids[xy]
        if region.grid_counts.model_dump() != planned[rid]["grid_counts"]:
            raise ValueError("MaterialMap counts differ from the diagnostic local grid")
        xs,ys = region.axis_lines("x"),region.axis_lines("y")
        for j,(b,d) in enumerate(zip(ys,ys[1:])):
            for i,(a,c) in enumerate(zip(xs,xs[1:])):
                cells[(rid,i,j)] = {"region_id":rid,"i":i,"j":j,
                    "x0_mm":a,"y0_mm":b,"x1_mm":c,"y1_mm":d}
    grid["cells"] = list(cells.values())
    return out
