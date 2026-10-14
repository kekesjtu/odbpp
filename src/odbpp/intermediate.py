"""The single current JSON contract; Pydantic is used only at IO boundaries.

Geometry kernels, feature records, rasters and equivalent cells remain their
existing dataclasses/arrays. No format versions or compatibility migrations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Mapping, Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, model_validator

Number = FiniteFloat
# A compact coordinate pair, not a separate Pydantic object per vertex. Both
# Python pairs and JSON arrays are accepted; strict finite elements remain.
Point = Annotated[tuple[Number, Number], Field(strict=False)]
Index = Annotated[StrictInt, Field(ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class PolygonModel(ContractModel):
    """A regularised positive boundary; island=False explicitly denotes a hole."""
    island: bool
    points: list[Point] = Field(min_length=3, description="毫米坐标；首点不在末尾重复；至少三个点。")


class DomainModel(ContractModel):
    id: str = Field(min_length=1)
    source: Literal["step_profile", "layer_profile"]
    polygons: list[PolygonModel]


class ZExtentModel(ContractModel):
    start_mm: Number = Field(ge=0)
    end_mm: Number = Field(ge=0)
    thickness_known: bool = Field(description="本层厚度与累计绝对 Z 联合可信；未知时仍输出 best-effort Z。")
    source: str = Field(default="unknown", min_length=1, description="绝对 Z 的来源；钻孔为跨层跨度，不代表独立厚度层。")

    @model_validator(mode="after")
    def ordered(self):
        if self.end_mm < self.start_mm:
            raise ValueError("end_mm must be >= start_mm")
        return self


class ThicknessModel(ContractModel):
    known: bool = Field(description="仅本层厚度是否有有效来源，不受上方未知厚度影响。")
    source: str = Field(min_length=1)
    # Raw provenance is deliberately NOT a second normalised thickness value.
    input_value: Number | None = None
    kind: Literal["thickness", "thickness_um", "areal_density_oz_ft2"] | None = None
    unit: Literal["mm", "inch", "um", "oz/ft2"] | None = None
    thickness_source: Literal["attribute", "assumed_density"] | None = None
    assumed_density_g_cm3: Number | None = Field(default=None, gt=0)
    spec_deviation: bool = False

    @model_validator(mode="after")
    def provenance(self):
        if self.kind == "areal_density_oz_ft2":
            if self.unit != "oz/ft2" or self.thickness_source != "assumed_density" or self.assumed_density_g_cm3 is None:
                raise ValueError("imperial copper weight requires oz/ft2, assumed_density and assumed_density_g_cm3")
        if self.kind == "thickness_um" and self.unit != "um":
            raise ValueError("metric copper weight requires unit um")
        if self.kind == "thickness" and self.unit not in ("mm", "inch"):
            raise ValueError("dielectric thickness requires unit mm or inch")
        if self.kind is not None and self.input_value is None:
            raise ValueError("thickness provenance requires input_value")
        return self


class ParseInfoModel(ContractModel):
    """Optional audit information; never used to reinterpret material polarity."""
    polarity: Literal["P", "N"]
    base_from_profile: bool
    feature_records: Index
    polarity_runs: Index
    drills_subtracted: list[str]
    drill_cut_mm2: Number = Field(ge=0)


class LayerModel(ContractModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1, description="ODB++ 层类型；等效器明确报告不支持的材料类型。")
    context: Literal["BOARD", "MISC"] = "BOARD"
    row: StrictInt = Field(default=1, ge=1)
    form: Literal["RIGID", "FLEX"] = "RIGID"
    form_source: Literal["DECLARED", "FLEX_SUBTYPE", "DEFAULT"] = "DEFAULT"
    geometry_status: Literal["ready", "failed", "omitted"] = "ready"
    geometry_error: str | None = None
    domain_id: str | None = None
    polygons: list[PolygonModel]
    z_mm: ZExtentModel | None = None
    thickness: ThicknessModel | None = None
    copper_index: Index | None = None
    copper_span: list[Index] | None = Field(default=None, min_length=2, max_length=2,
                                          description="自顶向下的两个铜层索引；不重复存储端点名称。")
    parse_info: ParseInfoModel | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.copper_span is not None and self.copper_span[0] >= self.copper_span[1]:
            raise ValueError("copper_span endpoints must increase")
        if self.geometry_status != "ready" and self.polygons:
            raise ValueError("failed/omitted geometry must not carry polygons")
        if self.geometry_status == "failed" and not self.geometry_error:
            raise ValueError("failed geometry requires geometry_error")
        return self


class StepModel(ContractModel):
    name: str = Field(min_length=1)
    profile_domain_id: str | None = Field(default=None, description="整板 step profile 在 domains 中的引用；不能以铜图案替代。")
    domains: list[DomainModel]
    layers: list[LayerModel] = Field(description="唯一层目录；包含失败及未导出层，按 matrix row 排列。")

    @model_validator(mode="after")
    def references(self):
        ids = [d.id for d in self.domains]
        names = [l.name for l in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate domain id")
        if len(names) != len(set(names)):
            raise ValueError("duplicate layer name")
        domains = {d.id: d for d in self.domains}
        indexed = [l for l in self.layers if l.copper_index is not None]
        copper = {l.copper_index for l in indexed}
        if len(copper) != len(indexed):
            raise ValueError("duplicate copper_index")
        if any(l.type not in ("SIGNAL", "POWER_GROUND", "MIXED") or l.context != "BOARD" for l in indexed):
            raise ValueError("copper_index must belong to a BOARD copper layer")
        if self.profile_domain_id is not None:
            if self.profile_domain_id not in domains or domains[self.profile_domain_id].source != "step_profile":
                raise ValueError("profile_domain_id must reference a step_profile domain")
        for layer in self.layers:
            if layer.domain_id is not None and layer.domain_id not in domains:
                raise ValueError(f"layer {layer.name!r}: unknown domain_id {layer.domain_id!r}")
            if layer.copper_span is not None and any(i not in copper for i in layer.copper_span):
                raise ValueError(f"layer {layer.name!r}: copper_span endpoints must reference existing copper_index values")
        return self


class ProductModel(ContractModel):
    name: str | None = None
    odb_version: str


class SourceModel(ContractModel):
    path: str
    job_root: str
    input_units: Literal["MM", "INCH"]
    product: ProductModel


class DiagnosticModel(ContractModel):
    level: Literal["warning", "error"]
    code: str
    message: str
    path: str | None = None


class BoardDocument(ContractModel):
    """One physical step, positive polygons, mm XY and absolute downward Z."""
    units: Literal["mm"]
    source: SourceModel | None = None
    resolution_mm: Number = Field(default=0.0001, gt=0,
                                  description="规整后坐标网格间距，单位 mm；非材料采样光栅。")
    steps: list[StepModel] = Field(min_length=1, max_length=1)
    diagnostics: list[DiagnosticModel] | None = None

    def internal_document(self) -> dict:
        """Drop the Pydantic containers without copying large coordinate lists.

        Consumers immediately construct their existing Ring/Region dataclasses.
        Returned mappings belong to this one computation; no validation in loops.
        """
        return {"units": self.units, "steps": [{
            "name": step.name, "profile_domain_id": step.profile_domain_id,
            "domains": [{"id": d.id, "source": d.source,
                         "polygons": [{"island": p.island, "points": p.points} for p in d.polygons]}
                        for d in step.domains],
            "layers": [{"name": l.name, "type": l.type, "context": l.context,
                        "row": l.row, "geometry_status": l.geometry_status,
                        "geometry_error": l.geometry_error, "domain_id": l.domain_id,
                        "copper_index": l.copper_index, "copper_span": l.copper_span,
                        "z_mm": None if l.z_mm is None else l.z_mm.model_dump(),
                        "polygons": [{"island": p.island, "points": p.points} for p in l.polygons]}
                       for l in step.layers]} for step in self.steps]}


def validate_document(document: BoardDocument | Mapping[str, Any]) -> BoardDocument:
    return document if isinstance(document, BoardDocument) else BoardDocument.model_validate(document)


def read_document(path: str | Path) -> BoardDocument:
    return BoardDocument.model_validate_json(Path(path).read_bytes())


def write_document(document: BoardDocument, path: str | Path) -> None:
    Path(path).write_text(document.model_dump_json(indent=1, exclude_none=True) + "\n", encoding="utf-8", newline="\n")


def write_schema(path: str | Path) -> None:
    """Generate the unversioned, human-readable JSON Schema from this contract."""
    import json
    schema = BoardDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    Path(path).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
