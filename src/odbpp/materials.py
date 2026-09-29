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

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

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
        if self.k_w_mk <= 0.0:
            raise ValueError(f"{self.key}: k must be positive, got {self.k_w_mk!r}")
        if self.density_kg_m3 <= 0.0:
            raise ValueError(
                f"{self.key}: density must be positive, got {self.density_kg_m3!r}")
        if self.specific_heat_j_kgk <= 0.0:
            raise ValueError(
                f"{self.key}: specific heat must be positive, got "
                f"{self.specific_heat_j_kgk!r}")

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
            values[field_name] = float(data[field_name])
        except (TypeError, ValueError):
            diagnostics.error(MATERIALS_INVALID,
                              f"{where}.{field_name}: {data[field_name]!r} is not "
                              f"a number")
            return None
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
        self.global_overrides: dict[str, _Override] = {}
        self.layer_overrides: dict[str, dict[str, _Override]] = {}
        self._warned_defaults: set[str] = set()
        if config:
            self._load(config)
        #: True when at least one phase ever fell back to the project default.
        self.used_project_default = False

    # ------------------------------------------------------------- loading

    def _load(self, config: Mapping[str, Any]) -> None:
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

    def _layer_default(self, phase: str) -> Material:
        key = phase
        if key not in self._warned_defaults:
            self._warned_defaults.add(key)
            self.used_project_default = True
            self.diagnostics.warn(
                MATERIALS_DEFAULT_USED,
                f"no user material configured for the {phase} phase; using the "
                f"project default {self.defaults[phase].k_w_mk:g} W/(m*K), "
                f"{self.defaults[phase].density_kg_m3:g} kg/m3, "
                f"{self.defaults[phase].specific_heat_j_kgk:g} J/(kg*K) "
                f"({self.defaults[phase].source})",
            )
        return self.defaults[phase]

    def resolve(self, layer_name: str) -> ResolvedMaterials:
        per_layer = self.layer_overrides.get(layer_name, {})
        provenance: dict[str, dict] = {}
        resolved: dict[str, Material] = {}
        for phase in ("copper", "base"):
            base = self.defaults[phase]
            origin = "project_default"
            if phase in self.global_overrides:
                base = self.global_overrides[phase].apply(base)
                origin = "user_global"
            if phase in per_layer:
                base = per_layer[phase].apply(base)
                origin = "layer_override"
            if origin == "project_default":
                base = self._layer_default(phase)
            resolved[phase] = base
            provenance[phase] = {
                "origin": origin,
                "source": base.source,
                "k_w_mk": base.k_w_mk,
                "density_kg_m3": base.density_kg_m3,
                "specific_heat_j_kgk": base.specific_heat_j_kgk,
            }
        return ResolvedMaterials(black=resolved["copper"], white=resolved["base"],
                                 provenance=provenance)

    def defaults_dict(self) -> dict:
        return {phase: material.as_dict()
                for phase, material in self.defaults.items()}
