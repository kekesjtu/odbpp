"""Region-based MaterialMap JSON; indexes and sampling state stay internal."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

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


class MaterialRegion(_Model):
    layer: LayerIdentity
    bounds_mm: Bounds
    x_lines_mm: list[FiniteFloat] = Field(min_length=2)
    y_lines_mm: list[FiniteFloat] = Field(min_length=2)
    values: list[Values]

    @model_validator(mode="after")
    def rectangular(self):
        for axis in "xy":
            lines = getattr(self, f"{axis}_lines_mm")
            if any(b <= a for a, b in zip(lines, lines[1:])):
                raise ValueError(f"{axis}_lines_mm must strictly increase")
            if (lines[0], lines[-1]) != getattr(self.bounds_mm, axis):
                raise ValueError(f"{axis} bounds must equal first/last grid line")
        expected = (len(self.x_lines_mm)-1)*(len(self.y_lines_mm)-1)
        if len(self.values) != expected:
            raise ValueError(f"region requires exactly {expected} row-major property rows, got {len(self.values)}")
        return self


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
        xs = sorted({x for r in self.regions for x in r.x_lines_mm})
        ys = sorted({y for r in self.regions for y in r.y_lines_mm})
        layers = {}
        for region in self.regions:
            for lines, all_lines in ((region.x_lines_mm, xs), (region.y_lines_mm, ys)):
                if lines != all_lines[bisect_left(all_lines, lines[0]):bisect_right(all_lines, lines[-1])]:
                    raise ValueError("region lines must follow the common global Cartesian axes")
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
    """Reconstruct geometry from lines, never from a serialized cell index."""
    for region in document.regions:
        nx = len(region.x_lines_mm)-1
        for q, values in enumerate(region.values):
            j, i = divmod(q, nx)
            yield region, (region.x_lines_mm[i], region.y_lines_mm[j],
                           region.x_lines_mm[i+1], region.y_lines_mm[j+1]), values


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
                "x_lines_mm": xs, "y_lines_mm": ys,
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
            for lines, axis in ((xs, result.grid.x_lines), (ys, result.grid.y_lines)):
                if lines != axis[bisect_left(axis, lines[0]):bisect_right(axis, lines[-1])]:
                    raise ValueError("serialized region is not a complete subset of the global axes")
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
    xi = {x: i for i, x in enumerate(grid["x_lines_mm"])}
    yi = {y: j for j, y in enumerate(grid["y_lines_mm"])}
    rects = out["rectification"]["rectangles"]
    ids = {(r["x0_mm"], r["y0_mm"], r["x1_mm"], r["y1_mm"]): n for n, r in enumerate(rects)}
    cells = {}
    for _region, bounds, _values in iter_cells(document):
        a, b, c, d = bounds
        xy = (_region.bounds_mm.x[0], _region.bounds_mm.y[0], _region.bounds_mm.x[1], _region.bounds_mm.y[1])
        cells[(a,b,c,d)] = {"region_id": ids[xy], "i": xi[a], "j": yi[b],
                           "x0_mm": a, "y0_mm": b, "x1_mm": c, "y1_mm": d}
    grid["cells"] = list(cells.values())
    return out
