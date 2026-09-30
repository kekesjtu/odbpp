"""Two-material model: copper (black) and base laminate (white).

AGENTS 等效器输入与材料规则 4-5.  The equivalence formulas need, for each of the
two phases, a thermal conductivity, a density and a specific heat.  The user may
set them globally or per layer; when they do not, this module supplies a
*project default with a recorded source* and says so out loud.

Precedence, highest first:

1. a per-layer override (``layers.<name>``);
2. a user global override (``global``);
3. the project default in this file.

The default is deliberately a plain, citable engineering value rather than a
fitted one.  It is not a measurement of the user's board, which is exactly why
falling back to it raises a warning instead of passing silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Sequence

from .diagnostics import MATERIALS_DEFAULT_USED, MATERIALS_INVALID, Diagnostics

__all__ = [
    "Material",
    "MaterialPair",
    "ResolvedMaterials",
    "MaterialBook",
    "DEFAULT_COPPER",
    "DEFAULT_BASE",
    "PROJECT_DEFAULTS",
]

#: Thermal data at ~25 C.  Copper is pure annealed copper (CRC Handbook of
#: Chemistry and Physics; k ~ 385 W/m/K, rho = 8.96 g/cm3, cp ~ 385 J/kg/K).
#: The density matches :data:`odbpp.units.DEFAULT_COPPER_DENSITY_G_CM3`, so the
#: thickness derived from an imperial copper weight and the equivalence use the
#: same assumption.
_COPPER_SOURCE = ("pure annealed copper at ~25 C; CRC Handbook of Chemistry "
                  "and Physics")

#: Standard FR-4 laminate.  Values are typical datasheet figures for a cured
#: FR-4 prepreg/core (k 0.29-0.35 W/m/K, rho ~1.85 g/cm3, cp ~1.0-1.2 kJ/kg/K),
#: e.g. Isola 370HR data sheet.  FR-4 is a family, not a compound, so the
#: default is a representative value and the per-layer override exists precisely
#: because a real stack can differ.
_BASE_SOURCE = "typical cured FR-4 datasheet values, e.g. Isola 370HR"


@dataclass(frozen=True)
class Material:
    """One homogeneous phase: conductivity, density and specific heat."""

    key: str
    label: str
    k_w_mk: float
    density_kg_m3: float
    specific_heat_j_kgk: float
    source: str

    def __post_init__(self) -> None:
        # A finite, strictly positive positive number: ``NaN`` and ``Inf`` must
        # never survive into the equivalence formulas, because they would turn
        # every output cell into a non-number while looking like a real value.
        for field_name, value in (("k_w_mk", self.k_w_mk),
                                  ("density_kg_m3", self.density_kg_m3),
                                  ("specific_heat_j_kgk",
                                   self.specific_heat_j_kgk)):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(
                    f"{self.key}: {field_name} must be a finite positive number, "
                    f"got {value!r}")

    def as_dict(self) -> dict:
        return {
            "name": self.key,
            "label": self.label,
            "k_w_mk": self.k_w_mk,
            "density_kg_m3": self.density_kg_m3,
            "specific_heat_j_kgk": self.specific_heat_j_kgk,
            "source": self.source,
        }


DEFAULT_COPPER = Material(
    key="copper",
    label="copper (black / material 1)",
    k_w_mk=385.0,
    density_kg_m3=8960.0,
    specific_heat_j_kgk=385.0,
    source=_COPPER_SOURCE,
)

DEFAULT_BASE = Material(
    key="base",
    label="base laminate (white / material 2)",
    k_w_mk=0.30,
    density_kg_m3=1850.0,
    specific_heat_j_kgk=1100.0,
    source=_BASE_SOURCE,
)

#: The project default pair, keyed by phase name.
PROJECT_DEFAULTS: dict[str, Material] = {
    "copper": DEFAULT_COPPER,
    "base": DEFAULT_BASE,
}


@dataclass(frozen=True)
class ResolvedMaterials:
    """The phase pair used for one layer, with where each value came from."""

    black: Material
    white: Material
    provenance: dict

    @property
    def k1(self) -> float:
        return self.black.k_w_mk

    @property
    def k2(self) -> float:
        return self.white.k_w_mk

    def as_dict(self) -> dict:
        return {
            "material_1_black": self.black.as_dict(),
            "material_2_white": self.white.as_dict(),
            "provenance": self.provenance,
        }


# Historical alias used in the derivation docs: material 1 is the black copper
# phase, material 2 the white base.
MaterialPair = ResolvedMaterials


@dataclass(frozen=True)
class _Override:
    k_w_mk: Optional[float] = None
    density_kg_m3: Optional[float] = None
    specific_heat_j_kgk: Optional[float] = None

    @property
    def is_empty(self) -> bool:
        return (self.k_w_mk is None and self.density_kg_m3 is None
                and self.specific_heat_j_kgk is None)

    def apply(self, base: Material) -> Material:
        return replace(
            base,
            k_w_mk=base.k_w_mk if self.k_w_mk is None else self.k_w_mk,
            density_kg_m3=(base.density_kg_m3 if self.density_kg_m3 is None
                           else self.density_kg_m3),
            specific_heat_j_kgk=(base.specific_heat_j_kgk
                                 if self.specific_heat_j_kgk is None
                                 else self.specific_heat_j_kgk),
            source=base.source,
        )


def _parse_override(data: Mapping[str, Any], where: str,
                    diagnostics: Diagnostics) -> Optional[_Override]:
    if not isinstance(data, Mapping):
        diagnostics.error(MATERIALS_INVALID,
                          f"{where}: expected an object, got {type(data).__name__}")
        return None
    allowed = {"k_w_mk", "density_kg_m3", "specific_heat_j_kgk"}
    unknown = set(data) - allowed
    if unknown:
        diagnostics.error(MATERIALS_INVALID,
                          f"{where}: unknown material field(s) "
                          f"{', '.join(sorted(unknown))}; expected "
                          f"{', '.join(sorted(allowed))}")
        return None
    values: dict[str, float] = {}
    for field_name in allowed:
        if field_name not in data or data[field_name] is None:
            continue
        try:
            value = float(data[field_name])
        except (TypeError, ValueError):
            diagnostics.error(MATERIALS_INVALID,
                              f"{where}.{field_name}: {data[field_name]!r} is not "
                              f"a number")
            return None
        if not math.isfinite(value) or value <= 0.0:
            # ``NaN``/``Inf`` slip past a bare ``<= 0`` check and ``0`` would
            # raise later, out of reach of the equivalent command's handler.
            diagnostics.error(
                MATERIALS_INVALID,
                f"{where}.{field_name}: {data[field_name]!r} must be a finite "
                f"positive number; the phase falls back to its project default")
            return None
        values[field_name] = value
    override = _Override(**values)
    return None if override.is_empty else override


class MaterialBook:
    """Resolves the phase pair for a layer with explicit provenance.

    ``config`` is the parsed user configuration (``None`` means "use project
    defaults everywhere").  Reading it never fails silently: a malformed field is
    an error diagnostic and the default is used, because the alternative --
    aborting a long run on a typo in an optional material file -- helps nobody,
    while silently substituting a wrong conductivity would.
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *,
                 diagnostics: Optional[Diagnostics] = None) -> None:
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics()
        self.defaults: dict[str, Material] = dict(PROJECT_DEFAULTS)
        #: Whether ``defaults.<phase>`` replaced the built-in project default.
        self._default_origin: dict[str, str] = {
            "copper": "project_default", "base": "project_default"}
        self.global_overrides: dict[str, _Override] = {}
        self.layer_overrides: dict[str, dict[str, _Override]] = {}
        self.type_overrides: dict[str, dict[str, _Override]] = {}
        self._warned_defaults: set[str] = set()
        if config:
            self._load(config)
        #: True when at least one phase ever fell back to the project default.
        self.used_project_default = False

    # ------------------------------------------------------------- loading

    def _load(self, config: Mapping[str, Any]) -> None:
        types = config.get("layer_types", {})
        if isinstance(types, Mapping):
            for kind, entry in types.items():
                if kind not in ("copper", "dielectric") or not isinstance(entry, Mapping):
                    self.diagnostics.error(MATERIALS_INVALID,
                                           f"layer_types.{kind}: expected copper/dielectric phase object")
                    continue
                self.type_overrides[kind] = {}
                for phase in ("copper", "base"):
                    if phase in entry:
                        override = _parse_override(entry[phase], f"layer_types.{kind}.{phase}",
                                                   self.diagnostics)
                        if override:
                            self.type_overrides[kind][phase] = override
        defaults = config.get("defaults")
        if isinstance(defaults, Mapping):
            for phase in ("copper", "base"):
                if phase in defaults:
                    override = _parse_override(defaults[phase],
                                               f"defaults.{phase}", self.diagnostics)
                    if override is not None:
                        base = self.defaults[phase]
                        self.defaults[phase] = replace(
                            override.apply(base),
                            source=f"user-provided default ({phase})")
                        self._default_origin[phase] = "user_default"

        glob = config.get("global")
        if isinstance(glob, Mapping):
            for phase in ("copper", "base"):
                if phase in glob:
                    override = _parse_override(glob[phase],
                                               f"global.{phase}", self.diagnostics)
                    if override is not None:
                        self.global_overrides[phase] = override

        layers = config.get("layers")
        if isinstance(layers, Mapping):
            for layer_name, entry in layers.items():
                if not isinstance(entry, Mapping):
                    self.diagnostics.error(
                        MATERIALS_INVALID,
                        f"layers.{layer_name}: expected an object")
                    continue
                resolved: dict[str, _Override] = {}
                for phase in ("copper", "base"):
                    if phase in entry:
                        override = _parse_override(
                            entry[phase], f"layers.{layer_name}.{phase}",
                            self.diagnostics)
                        if override is not None:
                            resolved[phase] = override
                if resolved:
                    self.layer_overrides[layer_name] = resolved

    # ------------------------------------------------------------ resolve

    def _layer_default(self, phase: str,
                       fields: Sequence[str]) -> Material:
        key = f"{phase}:{','.join(fields)}"
        if key not in self._warned_defaults:
            self._warned_defaults.add(key)
            self.used_project_default = True
            default = self.defaults[phase]
            self.diagnostics.warn(
                MATERIALS_DEFAULT_USED,
                f"no user material configured for the {phase} phase's "
                f"{', '.join(fields)}; using the project default "
                f"k={default.k_w_mk:g} W/(m*K), "
                f"density={default.density_kg_m3:g} kg/m3, "
                f"cp={default.specific_heat_j_kgk:g} J/(kg*K) "
                f"({default.source})",
            )
        return self.defaults[phase]

    _FIELDS = ("k_w_mk", "density_kg_m3", "specific_heat_j_kgk")

    def resolve(self, layer_name: str, layer_type: Optional[str] = None) -> ResolvedMaterials:
        per_layer = self.layer_overrides.get(layer_name, {})
        per_type = self.type_overrides.get(layer_type or "", {})
        provenance: dict[str, dict] = {}
        resolved: dict[str, Material] = {}
        for phase in ("copper", "base"):
            default = self.defaults[phase]
            default_origin = self._default_origin.get(phase, "project_default")
            # Per *field* precedence, so a user who overrides only copper's k is
            # not reported as if the untouched density and cp were also theirs.
            sources = (("user_global", self.global_overrides.get(phase)),
                       ("user_layer_type", per_type.get(phase)),
                       ("layer_override", per_layer.get(phase)))
            fields: dict[str, dict] = {}
            for field_name in self._FIELDS:
                value = getattr(default, field_name)
                origin = default_origin
                source = default.source
                for origin_name, override in sources:
                    if override is None:
                        continue
                    candidate = getattr(override, field_name)
                    if candidate is not None:
                        value = candidate
                        origin = origin_name
                        source = f"{origin_name} override"
                fields[field_name] = {"value": value, "origin": origin,
                                      "source": source}
            fallback = [name for name in self._FIELDS
                        if fields[name]["origin"] == "project_default"]
            if fallback:
                self._layer_default(phase, fallback)
            origins = {fields[name]["origin"] for name in self._FIELDS}
            if "layer_override" in origins:
                overall = "layer_override"
            elif "user_layer_type" in origins:
                overall = "user_layer_type"
            elif "user_global" in origins:
                overall = "user_global"
            elif "user_default" in origins:
                overall = "user_default"
            else:
                overall = "project_default"
            overridden = [name for name in self._FIELDS
                          if fields[name]["origin"] != default_origin]
            source = (default.source if not overridden else "; ".join(
                f"{name}={fields[name]['source']}" for name in overridden))
            material = Material(
                key=default.key, label=default.label,
                k_w_mk=fields["k_w_mk"]["value"],
                density_kg_m3=fields["density_kg_m3"]["value"],
                specific_heat_j_kgk=fields["specific_heat_j_kgk"]["value"],
                source=source,
            )
            resolved[phase] = material
            provenance[phase] = {
                "origin": overall,
                "source": source,
                "k_w_mk": fields["k_w_mk"]["value"],
                "density_kg_m3": fields["density_kg_m3"]["value"],
                "specific_heat_j_kgk": fields["specific_heat_j_kgk"]["value"],
                "fields": {name: dict(fields[name]) for name in self._FIELDS},
            }
        return ResolvedMaterials(black=resolved["copper"], white=resolved["base"],
                                 provenance=provenance)

    def defaults_dict(self) -> dict:
        return {phase: material.as_dict()
                for phase, material in self.defaults.items()}
