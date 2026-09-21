"""Unit handling for ODB++ v8.1.

ODB++ does **not** have one unit per file.  It has two parallel unit semantics
that coexist inside the same file, selected by the ``UNITS`` directive:

===================  ==================  =====================
quantity             ``UNITS=MM``        ``UNITS=INCH``
===================  ==================  =====================
coordinates          mm                  inch
distances            mm                  inch
layer-unit sizes     mm                  inch
symbol parameters    micrometre          mil  (1/1000 inch)
resize factors       micrometre          mil
drill/tool sizes     micrometre          mil
===================  ==================  =====================

Spec references: p.23-25 (units table), p.36 ("units for parameters that
represent size are expressed as a thousandth part of an inch or as a micron"),
p.166 ("resize factors for user-defined symbols are interpreted as mils or
microns"), p.174 (text ``xsize``/``ysize`` are in layer units).

Conflating the two is the classic 1000x bug, so this module exposes them as two
*separately named* methods, :meth:`UnitScope.coord_mm` and
:meth:`UnitScope.size_mm`.  There is deliberately no single ``to_mm``.

Two further traps, handled explicitly rather than with a generic helper:

``width_factor``
    Always expressed in units of 12 mil, regardless of the ``UNITS`` directive
    (p.174: "The width of character segment (in units of 12 mils); that is,
    1 = 12 mils, 0.5 = 6 mils").

``copper weight``
    The *physical quantity* changes with the directive (p.24): micrometres of
    thickness when metric, ounces per square foot (an areal density) when
    imperial.  An areal density is not a length, so :func:`copper_weight` keeps
    both meanings on :class:`CopperWeight` and states which one it holds.  For
    the imperial form it additionally derives a millimetre thickness from an
    assumed copper density (:data:`DEFAULT_COPPER_DENSITY_G_CM3`), because the
    stack's absolute Z needs a copper thickness and the format does not carry
    one.  That conversion is an assumption, so the density used is part of the
    result rather than a constant buried in the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union

from .errors import ParseError

__all__ = [
    "UnitSystem",
    "UnitScope",
    "DEFAULT_UNIT_SYSTEM",
    "CopperWeight",
    "copper_weight",
    "MM_PER_INCH",
    "MM_PER_MIL",
    "WIDTH_FACTOR_MIL",
    "DEFAULT_COPPER_DENSITY_G_CM3",
    "OUNCE_PER_SQ_FT_TO_G_PER_CM2",
]

MM_PER_INCH = 25.4
MM_PER_MIL = MM_PER_INCH / 1000.0  # 0.0254

#: ``width_factor`` is expressed in multiples of 12 mil (spec p.174).
WIDTH_FACTOR_MIL = 12.0

#: Density of electrodeposited copper in g/cm^3, used only to turn an imperial
#: ``.copper_weight`` (an areal density in oz/ft^2) into a thickness.  The
#: format stores neither the density nor the foil construction, so this is a
#: documented default -- 1 oz/ft^2 becomes ~34.1 um, which is the figure the
#: industry quotes for 1 oz copper.
DEFAULT_COPPER_DENSITY_G_CM3 = 8.96

#: 1 oz/ft^2 expressed as a mass per area.  1 oz = 28.349523125 g and
#: 1 ft^2 = 929.0304 cm^2.
OUNCE_PER_SQ_FT_TO_G_PER_CM2 = 28.349523125 / 929.0304


class UnitSystem(str, Enum):
    """The two measurement systems an ODB++ file can declare."""

    MM = "MM"
    INCH = "INCH"

    @classmethod
    def parse(cls, token: str, *, path: Optional[str] = None, line_no: Optional[int] = None) -> "UnitSystem":
        """Parse a ``UNITS`` value, raising :class:`ParseError` if unrecognised."""
        key = token.strip().upper()
        # Tolerate the historical spellings seen in the wild.
        aliases = {"MM": cls.MM, "METRIC": cls.MM, "INCH": cls.INCH,
                   "INCHES": cls.INCH, "IMPERIAL": cls.INCH, "MIL": cls.INCH}
        try:
            return aliases[key]
        except KeyError:
            raise ParseError(
                f"unrecognised UNITS value {token!r} (expected MM or INCH)",
                path=path,
                line_no=line_no,
            ) from None

    @property
    def coord_mm_per_unit(self) -> float:
        """Millimetres per coordinate unit."""
        return 1.0 if self is UnitSystem.MM else MM_PER_INCH

    @property
    def size_mm_per_unit(self) -> float:
        """Millimetres per *size* unit (micrometre or mil)."""
        return 0.001 if self is UnitSystem.MM else MM_PER_MIL

    @property
    def size_unit_name(self) -> str:
        return "um" if self is UnitSystem.MM else "mil"


#: Spec p.23: "If the default is not defined for the product model, the default
#: is imperial."
DEFAULT_UNIT_SYSTEM = UnitSystem.INCH


@dataclass(frozen=True)
class UnitScope:
    """Resolves measurements of one file into millimetres.

    A scope is created per file (``misc/info``, ``stephdr``, ``features``,
    ``tools``, ...) because a ``features`` file may override the product model
    default, and a single ``$`` symbol line may override the file again.
    """

    system: UnitSystem
    #: Where this scope came from, for error messages (e.g. "steps/pcb/profile").
    origin: Optional[str] = None

    # ---------------------------------------------------------------- coords

    def coord_mm(self, value: Union[int, float]) -> float:
        """Convert a coordinate or distance to millimetres.

        Use for ``x``/``y`` in every record, for ``OS``/``OC``/``OB`` points,
        and for the *layer-unit* sizes of text (``xsize``/``ysize``) and
        barcodes (``w``/``h``).
        """
        return float(value) * self.system.coord_mm_per_unit

    # ------------------------------------------------------------------ sizes

    def size_mm(self, value: Union[int, float]) -> float:
        """Convert a *size* value to millimetres.

        Use for standard symbol parameters (``r300`` -> 0.3 mm when metric),
        user-symbol resize factors, and drill ``DRILL_SIZE``/``FINISH_SIZE``.

        Do **not** use for coordinates: those go through :meth:`coord_mm`.
        """
        return float(value) * self.system.size_mm_per_unit

    def coord(self, value: Union[int, float]) -> float:
        """Alias of :meth:`coord_mm`, kept for terseness in hot loops."""
        return self.coord_mm(value)

    # ----------------------------------------------------------------- misc

    @staticmethod
    def width_factor_to_mm(factor: float) -> float:
        """``width_factor`` -> millimetres.

        Always 12-mil units, independent of ``UNITS`` (spec p.174).

        Currently unused in production code: ``width_factor`` only appears on
        ``T`` (text) records, which are out of scope (AGENTS constraint 6).  It
        is kept because the rule is a units rule, so this is where a future text
        implementation should look rather than rediscovering the trap.
        """
        return float(factor) * WIDTH_FACTOR_MIL * MM_PER_MIL

    @staticmethod
    def normalize_angle(degrees: float) -> float:
        """Normalise an angle into ``[0, 360)``.

        The spec requires positive angles in that range and states rotation is
        always clockwise (p.34).
        """
        return float(degrees) % 360.0

    # ------------------------------------------------------------------ misc

    @classmethod
    def metric(cls, origin: Optional[str] = None) -> "UnitScope":
        return cls(UnitSystem.MM, origin)

    @classmethod
    def imperial(cls, origin: Optional[str] = None) -> "UnitScope":
        return cls(UnitSystem.INCH, origin)

    @classmethod
    def default(cls, origin: Optional[str] = None) -> "UnitScope":
        return cls(DEFAULT_UNIT_SYSTEM, origin)

    def with_system(self, system: UnitSystem) -> "UnitScope":
        return UnitScope(system, self.origin)

    def describe(self) -> str:
        return (f"{self.system.value} (coords x{self.system.coord_mm_per_unit:g} mm, "
                f"sizes in {self.system.size_unit_name})")


# ---------------------------------------------------------------------------
# copper weight
# ---------------------------------------------------------------------------

class CopperWeightKind(str, Enum):
    """Physical quantity that a ``.layer_dielectric``/copper-weight value means."""

    #: ``UNITS=MM``: the value is a copper *thickness* in micrometres.
    THICKNESS_UM = "thickness_um"
    #: ``UNITS=INCH``: the value is an areal density in ounces per square foot.
    AREAL_DENSITY_OZ_FT2 = "areal_density_oz_ft2"


@dataclass(frozen=True)
class CopperWeight:
    """A copper weight/copper thickness value with its physical meaning pinned.

    The JSON intermediate format must carry this distinction, because a consumer
    cannot tell 1 oz/ft^2 from 1 um by looking at the number alone.  For the
    imperial form a thickness is also offered, derived from
    :attr:`density_g_cm3`; it is an assumption and the field names say so.
    """

    raw: float
    kind: CopperWeightKind
    system: UnitSystem
    #: Density assumed when translating an areal density into a thickness.
    density_g_cm3: float = DEFAULT_COPPER_DENSITY_G_CM3

    @property
    def thickness_mm(self) -> float:
        """Thickness in millimetres.

        Metric values are already a thickness (micrometres).  Imperial values
        are an areal density, so the thickness is ``density / copper density``
        -- 1 oz/ft^2 is about 0.0341 mm at 8.96 g/cm^3.
        """
        if self.kind is CopperWeightKind.THICKNESS_UM:
            return self.raw * 0.001
        return (self.raw * OUNCE_PER_SQ_FT_TO_G_PER_CM2
                / self.density_g_cm3 * 10.0)

    @property
    def is_thickness_derived(self) -> bool:
        """True when :attr:`thickness_mm` came from an assumed density."""
        return self.kind is CopperWeightKind.AREAL_DENSITY_OZ_FT2

    @property
    def ounces_per_square_foot(self) -> Optional[float]:
        """Areal density in oz/ft^2, or ``None`` when the value is a thickness."""
        if self.kind is CopperWeightKind.AREAL_DENSITY_OZ_FT2:
            return self.raw
        return None

    def as_dict(self) -> dict:
        out: dict = {
            "value": self.raw,
            "kind": self.kind.value,
            "unit": "um" if self.kind is CopperWeightKind.THICKNESS_UM else "oz/ft2",
            "thickness_mm": self.thickness_mm,
        }
        if self.kind is CopperWeightKind.AREAL_DENSITY_OZ_FT2:
            # Spelled out rather than implied: the density is not in the file,
            # so a consumer auditing the number has to see which one was used.
            out["thickness_source"] = "assumed_density"
            out["assumed_density_g_cm3"] = self.density_g_cm3
        return out


def copper_weight(value: float, system: UnitSystem,
                  density_g_cm3: float = DEFAULT_COPPER_DENSITY_G_CM3) -> CopperWeight:
    """Interpret a copper weight field according to the active unit system.

    Spec p.24: "In metric units, copper weight is actually copper thickness."
    The two kinds are still kept apart -- :attr:`CopperWeight.kind` and
    :attr:`CopperWeight.unit` say which quantity the authored number is -- but
    an imperial density now also yields a thickness through *density_g_cm3*,
    which the caller may override.  The default is not a measurement: it is the
    standard density of copper, and the result records that it was assumed.
    """
    kind = (CopperWeightKind.THICKNESS_UM if system is UnitSystem.MM
            else CopperWeightKind.AREAL_DENSITY_OZ_FT2)
    if float(value) < 0.0:
        raise ValueError(f"copper weight cannot be negative: {value!r}")
    if float(density_g_cm3) <= 0.0:
        raise ValueError(f"copper density must be positive: {density_g_cm3!r}")
    return CopperWeight(float(value), kind, system, float(density_g_cm3))
