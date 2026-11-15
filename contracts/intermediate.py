"""The single current JSON contract; Pydantic is used only at IO boundaries.

Geometry kernels, feature records, rasters and equivalent cells remain their
existing dataclasses/arrays. No format versions or compatibility migrations.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Mapping, Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, model_serializer, model_validator

Number = FiniteFloat
# A compact coordinate pair, not a separate Pydantic object per vertex. Both
# Python pairs and JSON arrays are accepted; strict finite elements remain.
Point = Annotated[tuple[Number, Number], Field(strict=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class PolygonModel(ContractModel):
    """A regularised positive boundary; island=False explicitly denotes a hole."""
    island: bool
    points: list[Point] = Field(min_length=3, description="毫米坐标；首点不在末尾重复；至少三个点。")


class ProfileModel(ContractModel):
    id: str = Field(min_length=1)
    layers: list[str] = Field(description="使用此轮廓的 BOARD 层名称；每层最多归属一个轮廓。")
    polygons: list[PolygonModel]


class ZExtentModel(ContractModel):
    """A physical layer's own position on the absolute Z axis.

    ``null`` expresses "not measured" instead of a boolean flag, so no estimate
    can be read as a measurement: a null ``start_mm`` says the layers above do
    not all state their thickness, a null ``thickness_mm`` says this layer's
    own thickness was never stated. ``0.0`` stays a real measurement, and the
    two sides are independent -- a known thickness does not imply a known
    position.
    """
    start_mm: Number | None = Field(default=None, ge=0, description="本层顶面绝对 Z，自叠层顶面向下；null 表示累计位置不可信。")
    thickness_mm: Number | None = Field(default=None, ge=0, description="本层自身厚度；null 表示格式未声明。")

    @model_serializer(mode="plain")
    def explicit_unknown(self):
        """Always emit both keys: an absent key cannot be told from a null one."""
        return {"start_mm": self.start_mm, "thickness_mm": self.thickness_mm}


class LayerModel(ContractModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1, description="ODB++ 层类型；等效器明确报告不支持的材料类型。")
    context: Literal["BOARD"] = "BOARD"
    row: StrictInt = Field(ge=1, description="matrix ROW；同一 step 内唯一，表达层的上下次序。")
    geometry_status: Literal["ready", "failed", "omitted"] = "ready"
    geometry_error: str | None = None
    polygons: list[PolygonModel] = Field(description="本层图元规整后的正向几何；介质不填充轮廓基底，跨层 DRILL 投影保持独立。")
    z_mm: ZExtentModel | None = Field(default=None, description="实体物理层自身的绝对 Z；DRILL 不保存此字段，由起止铜层推导。")
    start_layer: str | None = Field(default=None, min_length=1, description="DRILL 起始铜层名称。")
    end_layer: str | None = Field(default=None, min_length=1, description="DRILL 终止铜层名称。")

    @model_validator(mode="after")
    def consistent(self):
        if self.type == "DRILL" and self.z_mm is not None:
            raise ValueError("DRILL must not carry z_mm; derive it from endpoint copper layers")
        if self.type != "DRILL" and (self.start_layer is not None or self.end_layer is not None):
            raise ValueError("start_layer/end_layer require a DRILL layer")
        if self.geometry_status != "ready" and self.polygons:
            raise ValueError("failed/omitted geometry must not carry polygons")
        if self.geometry_status == "failed" and not self.geometry_error:
            raise ValueError("failed geometry requires geometry_error")
        return self


class StepModel(ContractModel):
    name: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, description="整板轮廓在 profiles 中的引用；不能以铜图案替代。")
    profiles: list[ProfileModel]
    layers: list[LayerModel] = Field(description="BOARD 层目录；包含失败及未导出层，按 matrix row 排列。")

    @model_validator(mode="after")
    def references(self):
        ids = [d.id for d in self.profiles]
        names = [l.name for l in self.layers]
        rows = [l.row for l in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate profile id")
        if len(names) != len(set(names)):
            raise ValueError("duplicate layer name")
        if len(rows) != len(set(rows)):
            raise ValueError("duplicate matrix row")
        copper = {l.name: l for l in self.layers
                  if l.type in ("SIGNAL", "POWER_GROUND", "MIXED") and l.context == "BOARD"}
        if self.profile_id is not None and self.profile_id not in ids:
            raise ValueError("unknown step profile_id")
        assigned = set()
        for profile in self.profiles:
            for name in profile.layers:
                if name not in names:
                    raise ValueError(f"profile {profile.id!r}: unknown layer {name!r}")
                if name in assigned:
                    raise ValueError(f"layer {name!r}: multiple profile assignments")
                assigned.add(name)
        for layer in self.layers:
            for endpoint in (layer.start_layer, layer.end_layer):
                if endpoint is not None and endpoint not in copper:
                    raise ValueError(f"layer {layer.name!r}: endpoint {endpoint!r} must reference an existing BOARD copper layer")
            if layer.start_layer is not None and layer.end_layer is not None:
                if copper[layer.start_layer].row >= copper[layer.end_layer].row:
                    raise ValueError(f"layer {layer.name!r}: drill endpoints must follow matrix rows")
        return self


class ProductModel(ContractModel):
    name: str | None = None
    odb_version: str


class SourceModel(ContractModel):
    path: str
    job_root: str
    input_units: Literal["MM", "INCH"]
    product: ProductModel


class BoardDocument(ContractModel):
    """One physical step, positive polygons, mm XY and absolute downward Z."""
    units: Literal["mm"]
    source: SourceModel | None = None
    resolution_mm: Number = Field(default=0.0001, gt=0,
                                  description="规整后坐标网格间距，单位 mm；非材料采样光栅。")
    steps: list[StepModel] = Field(min_length=1, max_length=1)

    def internal_document(self) -> dict:
        """Drop the Pydantic containers without copying large coordinate lists.

        Consumers immediately construct their existing Ring/Region dataclasses.
        Returned mappings belong to this one computation; no validation in loops.
        """
        return {"units": self.units, "steps": [{
            "name": step.name, "profile_id": step.profile_id,
            "profiles": [{"id": d.id, "layers": d.layers,
                         "polygons": [{"island": p.island, "points": p.points} for p in d.polygons]}
                        for d in step.profiles],
            "layers": [{"name": l.name, "type": l.type, "context": l.context,
                        "row": l.row, "geometry_status": l.geometry_status,
                        "geometry_error": l.geometry_error,
                        "start_layer": l.start_layer, "end_layer": l.end_layer,
                        "z_mm": None if l.z_mm is None else l.z_mm.model_dump(),
                        "polygons": [{"island": p.island, "points": p.points} for p in l.polygons]}
                       for l in step.layers]} for step in self.steps]}


def validate_document(document: BoardDocument | Mapping[str, Any]) -> BoardDocument:
    return document if isinstance(document, BoardDocument) else BoardDocument.model_validate(document)


def read_document(path: str | Path) -> BoardDocument:
    return BoardDocument.model_validate_json(Path(path).read_bytes())


def _is_leaf(value: Any) -> bool:
    """Scalars and coordinate arrays stay on one line; records and members keep their lines."""
    if isinstance(value, dict):
        return not value
    if not isinstance(value, list):
        return True
    return all(not isinstance(v, (dict, list))
               or (isinstance(v, list) and not any(isinstance(c, (dict, list)) for c in v))
               for v in value)


def _render(value: Any, depth: int) -> str:
    """Indent structure, never coordinates: a case holds millions of numbers."""
    if _is_leaf(value):
        return json.dumps(value, ensure_ascii=False)
    pad = " " * (depth + 1)
    if isinstance(value, list):
        items = ",\n".join(pad + _render(v, depth + 1) for v in value)
        return "[\n" + items + "\n" + " " * depth + "]"
    items = ",\n".join(f'{pad}{json.dumps(k, ensure_ascii=False)}: {_render(v, depth + 1)}'
                       for k, v in value.items())
    return "{\n" + items + "\n" + " " * depth + "}"


def write_document(document: BoardDocument, path: str | Path) -> None:
    text = _render(document.model_dump(exclude_none=True), 0) + "\n"
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def write_schema(path: str | Path) -> None:
    """Generate the unversioned, human-readable JSON Schema from this contract."""
    schema = BoardDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    Path(path).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
