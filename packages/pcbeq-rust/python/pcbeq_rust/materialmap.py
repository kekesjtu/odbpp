"""Region-based MaterialMap JSON; indexes and sampling state stay internal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
def uniform_lines(low, high, count):
    return [low]+[low+(high-low)*i/count for i in range(1,count)]+[high]

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
