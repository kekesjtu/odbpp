"""Standard symbol grammar and the user-defined symbol library.

Spec: Appendix A (p.187-206) for the standard symbols, p.36-37 for the naming
grammar and user-defined symbols, p.92 for ``symbols/<name>/features``.

Why this module exists as a separate concern
--------------------------------------------

A ``features`` file references symbols by name, and the name alone decides
whether the shape is built in (``rect30x20``) or must be loaded from
``symbols/`` (``special_0.770x0.840``).  The spec's rule is grammar-based:

    <symbol_name_prefix>[<parameter_value>[x<parameter_value>[...]]]

so recognition must be a **strict parse of the whole name**, never a prefix
match.  ``cases/designodb-rigidflex`` contains nine user-defined symbols whose
names begin with ``s`` -- the reserved square prefix -- so a "starts with `s`"
heuristic silently mis-renders them as squares.  See
``docs/research/03-case-inventory.md`` §4.2(a).

User-defined symbols may reference other user-defined symbols, so loading is
recursive and needs cycle detection (the spec forbids cycles at p.37, but a
parser that trusts that will hang on malformed input).  Loading is also **lazy**:
of the 123 symbol definitions shipped in that same case, only 9 are referenced
by any layer, so eagerly expanding the library is 13x wasted work.
"""

from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass
from typing import Iterable, Optional, Union

from .archive import OdbArchive
from .diagnostics import (
    SYMBOL_SHADOWS_STANDARD,
    SYMBOL_UNKNOWN,
    Diagnostics,
)
from .errors import SymbolCycleError
from .features import FeatureFile, SymbolRef, parse_feature_file
from .units import UnitScope, UnitSystem

__all__ = [
    "StandardSymbol",
    "SymbolSpec",
    "STANDARD_SPECS",
    "parse_standard_symbol",
    "is_standard_symbol",
    "SymbolDefinition",
    "SymbolLibrary",
]


# ---------------------------------------------------------------------------
# standard symbol grammar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolSpec:
    """Parameter layout of one standard symbol family.

    ``names`` are the mandatory leading parameters in order.  ``optional`` names
    the trailing parameters that may be omitted (e.g. ``hplate`` corner radii).
    ``min_params`` defaults to ``len(names)``; the maximum is
    ``len(names) + len(optional)``.
    """

    prefix: str
    names: tuple[str, ...]
    optional: tuple[str, ...] = ()
    radius: bool = False          # trailing ``xr<rad>``
    chamfer: bool = False         # trailing ``xc<rad>``
    corners: bool = False         # corner bitmask after radius/chamfer
    style: bool = False           # trailing bare ``r`` or ``s``
    plating: bool = False         # ``hole``: p|n|v in the second slot
    min_params: Optional[int] = None

    @property
    def min_count(self) -> int:
        return self.min_params if self.min_params is not None else len(self.names)

    @property
    def max_count(self) -> int:
        return len(self.names) + len(self.optional)

    @property
    def param_names(self) -> tuple[str, ...]:
        return self.names + self.optional


#: Every standard symbol family, from Appendix A (spec p.187-206).
STANDARD_SPECS: tuple[SymbolSpec, ...] = (
    # --- basic ------------------------------------------------------------
    SymbolSpec("r", ("d",)),
    SymbolSpec("s", ("s",)),
    SymbolSpec("rect", ("w", "h"), radius=True, chamfer=True, corners=True),
    SymbolSpec("di", ("w", "h")),
    # ``oct``/``hex_*`` take a corner size that may be omitted, in which
    # case the shape is the regular polygon inscribed in the w x h box.
    SymbolSpec("oct", ("w", "h", "r"), min_params=2),
    SymbolSpec("el", ("w", "h")),
    SymbolSpec("tri", ("base", "h")),
    SymbolSpec("oval", ("w", "h")),
    SymbolSpec("oval_h", ("w", "h")),
    SymbolSpec("moire", ("rw", "rg", "nr", "lw", "ll", "la")),
    SymbolSpec("ext", ("n",)),
    # --- donuts -----------------------------------------------------------
    SymbolSpec("donut_r", ("od", "id")),
    SymbolSpec("donut_s", ("od", "id"), radius=True, chamfer=True, corners=True),
    SymbolSpec("donut_sr", ("od", "id")),
    SymbolSpec("donut_rc", ("ow", "oh", "lw"), radius=True, chamfer=True, corners=True),
    SymbolSpec("donut_o", ("ow", "oh", "lw")),
    # --- polygons ---------------------------------------------------------
    SymbolSpec("hex_l", ("w", "h", "r"), min_params=2),
    SymbolSpec("hex_s", ("w", "h", "r"), min_params=2),
    SymbolSpec("bfr", ("d",)),
    SymbolSpec("bfs", ("s",)),
    # --- thermals ---------------------------------------------------------
    SymbolSpec("thr", ("od", "id", "angle", "num_spokes", "gap")),
    SymbolSpec("ths", ("od", "id", "angle", "num_spokes", "gap")),
    SymbolSpec("s_ths", ("os", "is", "angle", "num_spokes", "gap"),
               radius=True, chamfer=True, corners=True),
    SymbolSpec("s_tho", ("od", "id", "angle", "num_spokes", "gap")),
    SymbolSpec("s_thr", ("os", "is", "angle", "num_spokes", "gap")),
    SymbolSpec("sr_ths", ("os", "id", "angle", "num_spokes", "gap")),
    SymbolSpec("rc_ths", ("ow", "oh", "angle", "num_spokes", "gap", "air_gap"),
               radius=True, chamfer=True, corners=True),
    SymbolSpec("rc_tho", ("w", "h", "angle", "num_spokes", "gap", "air_gap")),
    SymbolSpec("o_ths", ("ow", "oh", "angle", "num_spokes", "gap", "lw")),
    SymbolSpec("oblong_ths", ("ow", "oh", "angle", "num_spokes", "gap", "lw"),
               style=True),
    # --- solder-stencil symbols (p.197-202) --------------------------------
    SymbolSpec("hplate", ("w", "h", "c"), optional=("ra", "ro"), min_params=3),
    SymbolSpec("rhplate", ("w", "h", "c"), optional=("ra", "ro"), min_params=3),
    SymbolSpec("fhplate", ("w", "h", "vc", "hc"), optional=("ra", "ro"), min_params=4),
    SymbolSpec("radhplate", ("w", "h", "ms"), optional=("ra",), min_params=3),
    SymbolSpec("dshape", ("w", "h", "r"), optional=("ra",), min_params=3),
    SymbolSpec("cross", ("w", "h", "hs", "vs", "hc", "vc"), optional=("ra",),
               style=True, min_params=6),
    SymbolSpec("dogbone", ("w", "h", "hs", "vs", "hc"), optional=("ra",),
               style=True, min_params=5),
    SymbolSpec("dpack", ("w", "h", "hg", "vg", "hn", "vn"), optional=("ra",),
               min_params=6),
    # --- drill wheels ------------------------------------------------------
    SymbolSpec("hole", ("d", "tp", "tm"), plating=True, min_params=3),
)

#: Longest prefix first, so ``sr_ths`` wins over ``s`` and ``rect`` over ``r``.
_SPECS_BY_LENGTH: tuple[SymbolSpec, ...] = tuple(
    sorted(STANDARD_SPECS, key=lambda spec: len(spec.prefix), reverse=True)
)

_SPEC_BY_PREFIX: dict[str, SymbolSpec] = {spec.prefix: spec for spec in STANDARD_SPECS}

_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
_RADIUS = re.compile(r"^r(\d+\.?\d*|\.\d+)$")
_CHAMFER = re.compile(r"^c(\d+\.?\d*|\.\d+)$")
_CORNERS = re.compile(r"^\d+$")
_PLATING = re.compile(r"^[pnvPNV]$")


@dataclass(frozen=True)
class StandardSymbol:
    """A standard symbol expanded from its name (spec p.36).

    ``numbers`` are raw as authored; use :meth:`size_mm` to convert with the
    *size* unit semantic (micrometre or mil) rather than the coordinate one.
    """

    name: str
    prefix: str
    numbers: tuple[float, ...] = ()
    param_names: tuple[str, ...] = ()
    radius: Optional[float] = None
    chamfer: Optional[float] = None
    corners: tuple[int, ...] = ()
    style: Optional[str] = None
    plating: Optional[str] = None
    unit: UnitSystem = UnitSystem.MM

    @property
    def spec(self) -> SymbolSpec:
        return _SPEC_BY_PREFIX[self.prefix]

    @property
    def params(self) -> dict[str, float]:
        """Named parameters, including radius/chamfer when present."""
        out = dict(zip(self.param_names, self.numbers))
        if self.radius is not None:
            out["radius"] = self.radius
        if self.chamfer is not None:
            out["chamfer"] = self.chamfer
        return out

    def param(self, name: str, default: Optional[float] = None) -> Optional[float]:
        return self.params.get(name, default)

    def size_mm(self, value: float) -> float:
        """Convert one parameter to millimetres (micrometre/mil semantics)."""
        return UnitScope(self.unit).size_mm(value)

    @property
    def sizes_mm(self) -> tuple[float, ...]:
        return tuple(self.size_mm(v) for v in self.numbers)

    @property
    def radius_mm(self) -> Optional[float]:
        return None if self.radius is None else self.size_mm(self.radius)

    @property
    def chamfer_mm(self) -> Optional[float]:
        return None if self.chamfer is None else self.size_mm(self.chamfer)

    def is_symmetric(self) -> bool:
        """Whether the symbol can draw a line/arc of well-defined width.

        Spec p.36: only symmetric symbols (round, or square with w == h) may be
        used to draw lines and arcs.
        """
        p = self.params
        if self.prefix == "r":
            return True
        if self.prefix == "s":
            return True
        for a, b in (("w", "h"), ("ow", "oh"), ("od", "id"), ("os", "is")):
            if a in p and b in p:
                return abs(p[a] - p[b]) < 1e-9
        return False


def _strip_unit_markers(name: str) -> tuple[str, Optional[UnitSystem]]:
    """Remove a trailing ``I``/``M`` marker from a raw symbol name.

    The marker is normally a separate token on the ``$`` line and is split off by
    the feature parser, but callers holding a raw name should still work.
    """
    tokens = name.split()
    unit: Optional[UnitSystem] = None
    while len(tokens) > 1 and tokens[-1].upper() in ("I", "M"):
        unit = UnitSystem.INCH if tokens[-1].upper() == "I" else UnitSystem.MM
        tokens.pop()
    return " ".join(tokens), unit


@lru_cache(maxsize=4096)
def parse_standard_symbol(
    name: str,
    unit: UnitSystem = UnitSystem.MM,
) -> Optional[StandardSymbol]:
    """Parse *name* as a standard symbol, or return ``None``.

    ``None`` means "this is not a standard symbol" -- the caller should look the
    name up in the ``symbols/`` directory.  An empty return is *not* an error:
    every user-defined symbol takes this path.
    """
    stripped, marker_unit = _strip_unit_markers(name)
    if marker_unit is not None:
        unit = marker_unit

    if not stripped:
        return None

    for spec in _SPECS_BY_LENGTH:
        if not stripped.startswith(spec.prefix):
            continue

        remainder = stripped[len(spec.prefix):]
        if not remainder:
            # e.g. the bare name "r": a prefix with no parameters at all.
            continue

        parsed = _parse_parameters(spec, remainder, stripped, unit)
        if parsed is not None:
            return parsed

    return None


def _parse_parameters(
    spec: SymbolSpec,
    remainder: str,
    full_name: str,
    unit: UnitSystem,
) -> Optional[StandardSymbol]:
    """Parse the ``<n>x<n>x...`` tail of *remainder*.  ``None`` if malformed."""
    tokens = remainder.split("x")
    if not tokens or not _NUMBER.match(tokens[0]):
        return None

    # ``hole`` puts a plating letter in the second slot: hole<d>x<p>x<tp>x<tm>
    plating: Optional[str] = None
    if spec.plating:
        if len(tokens) < 2 or not _PLATING.match(tokens[1]):
            return None
        plating = tokens[1].lower()
        tokens = [tokens[0]] + tokens[2:]

    numbers: list[float] = []
    radius: Optional[float] = None
    chamfer: Optional[float] = None
    corners: tuple[int, ...] = ()
    style: Optional[str] = None

    index = 0
    # --- leading numeric parameters ---------------------------------------
    while index < len(tokens) and _NUMBER.match(tokens[index]):
        numbers.append(float(tokens[index]))
        index += 1

    # --- trailing radius / chamfer, then the corner bitmask ----------------
    if index < len(tokens):
        match = _RADIUS.match(tokens[index])
        if match:
            if not spec.radius:
                return None
            radius = float(match.group(1))
            index += 1
        else:
            match = _CHAMFER.match(tokens[index])
            if match:
                if not spec.chamfer:
                    return None
                chamfer = float(match.group(1))
                index += 1

    if index < len(tokens) and _CORNERS.match(tokens[index]) and (radius is not None or chamfer is not None):
        if not spec.corners:
            return None
        corners = tuple(int(ch) for ch in tokens[index])
        index += 1

    # --- trailing line style (cross / dogbone / oblong_ths) ----------------
    if index < len(tokens) and tokens[index].lower() in ("r", "s") and spec.style:
        style = tokens[index].lower()
        index += 1

    # --- parameters that the spec places after the style letter --------------
    # e.g. ``dogbone<...>x[r|s]x<ra>`` and ``cross<...>x[r|s]x<ra>``.
    while index < len(tokens) and _NUMBER.match(tokens[index]):
        numbers.append(float(tokens[index]))
        index += 1

    # --- validation --------------------------------------------------------
    if index != len(tokens):
        return None
    if not spec.min_count <= len(numbers) <= spec.max_count:
        return None
    if len(corners) > 4:
        return None

    return StandardSymbol(
        name=full_name,
        prefix=spec.prefix,
        numbers=tuple(numbers),
        param_names=spec.param_names[:len(numbers)],
        radius=radius,
        chamfer=chamfer,
        corners=corners,
        style=style,
        plating=plating,
        unit=unit,
    )


def is_standard_symbol(name: str) -> bool:
    """Whether *name* parses as a standard symbol.

    This is the single source of truth for the standard/custom decision, so that
    the feature parser, the symbol library and the geometry layer cannot drift.
    """
    return parse_standard_symbol(name) is not None


# ---------------------------------------------------------------------------
# user-defined symbols
# ---------------------------------------------------------------------------

@dataclass
class SymbolDefinition:
    """A parsed ``symbols/<name>/features`` definition."""

    name: str
    #: Actual directory name (may differ in case from ``name``).
    dir_name: str
    features: FeatureFile

    @property
    def feature_count(self) -> int:
        return len(self.features.records)


SymbolTarget = Union[StandardSymbol, SymbolDefinition]


class SymbolLibrary:
    """Lazy, cycle-checked access to ``<root>/symbols/``."""

    def __init__(
        self,
        archive: OdbArchive,
        root: str,
        *,
        diagnostics: Diagnostics,
        default_scope: UnitScope,
        definitions=None,
    ) -> None:
        self.archive = archive
        self.root = root
        self.diagnostics = diagnostics
        self.default_scope = default_scope
        self.definitions = definitions

        self._cache: dict[str, SymbolDefinition] = {}
        self._missing: set[str] = set()
        self._dir_names: Optional[dict[str, str]] = None

    # ------------------------------------------------------------- discovery

    @property
    def dir_names(self) -> dict[str, str]:
        """Case-folded symbol name -> actual directory name."""
        if self._dir_names is None:
            mapping: dict[str, str] = {}
            rel = f"{self.root}/symbols" if self.root else "symbols"
            if self.archive.is_dir(rel):
                for entry in self.archive.list_dir(rel):
                    child = f"{rel}/{entry}"
                    if self.archive.exists(f"{child}/features"):
                        mapping.setdefault(entry.casefold(), entry)
            self._dir_names = mapping
        return self._dir_names

    @property
    def names(self) -> list[str]:
        """Symbol directory names present on disk, sorted."""
        return sorted(self.dir_names.values())

    def has(self, name: str) -> bool:
        return name.casefold() in self.dir_names

    def __len__(self) -> int:
        return len(self.dir_names)

    def __contains__(self, name: str) -> bool:
        return self.has(name)

    # ---------------------------------------------------------------- loading

    def definition(self, name: str) -> Optional[SymbolDefinition]:
        """Parse and cache ``symbols/<name>/features``.  ``None`` if absent."""
        key = name.casefold()

        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if key in self._missing:
            return None

        dir_name = self.dir_names.get(key)
        if dir_name is None:
            self._missing.add(key)
            self.diagnostics.warn(
                SYMBOL_UNKNOWN,
                f"referenced user-defined symbol {name!r} has no symbols/ directory",
                self.root or None,
            )
            return None

        rel = f"{self.root}/symbols/{dir_name}/features" if self.root \
            else f"symbols/{dir_name}/features"
        features = parse_feature_file(
            self.archive, rel,
            default_scope=self.default_scope,
            diagnostics=self.diagnostics,
            definitions=self.definitions,
        )
        definition = SymbolDefinition(name=name, dir_name=dir_name, features=features)
        self._cache[key] = definition
        return definition

    # --------------------------------------------------------------- resolve

    def resolve(self, ref: SymbolRef, file_unit: UnitSystem) -> Optional[SymbolTarget]:
        """Resolve a feature's symbol reference to a standard or custom symbol.

        Standard wins when a name parses as such, because the spec reserves those
        prefixes for standard symbols (p.37).  A colliding directory is reported
        rather than silently preferred.
        """
        unit = ref.effective_unit(file_unit)

        standard = parse_standard_symbol(ref.name, unit)
        if standard is not None:
            if self.has(ref.name):
                self.diagnostics.warn(
                    SYMBOL_SHADOWS_STANDARD,
                    f"{ref.name!r} parses as a standard symbol but a symbols/ "
                    f"directory with that name also exists; the standard "
                    f"definition takes precedence per spec p.37",
                    self.root or None,
                )
            return standard

        return self.definition(ref.name)

    def resolve_name(self, name: str, unit: UnitSystem) -> Optional[SymbolTarget]:
        return self.resolve(SymbolRef(index=0, name=name), unit)

    # ------------------------------------------------------------- preloading

    def preload(self, names: Iterable[str]) -> list[str]:
        """Parse every definition reachable from *names*, recursively.

        A :class:`SymbolLibrary` reads from an :class:`~pcbkit.archive.OdbArchive`
        lazily, and the archive is closed when loading finishes.  Detaching
        therefore means materialising the definitions that will be needed, so a
        :class:`~pcbkit.model.Job` never holds a file handle and stays usable for
        rendering long after the archive is gone.

        Only reachable definitions are parsed.  ``cases/designodb-rigidflex``
        ships 123 definitions but its layers reference 9, and eager loading
        everything would be 13x wasted work for no benefit.

        Returns the names that were actually loaded.
        """
        loaded: list[str] = []
        seen: set[str] = set()

        def visit(name: str) -> None:
            key = name.casefold()
            if key in seen:
                return
            seen.add(key)

            definition = self.definition(name)
            if definition is None:
                return
            loaded.append(definition.name)

            # Children first, so a dependency chain is fully resident.
            for child in self.custom_references(definition.features,
                                                self.default_scope.system):
                visit(child)

        for name in names:
            visit(name)
        return loaded

    # ------------------------------------------------------------- recursion

    def expand_order(self, name: str) -> list[SymbolDefinition]:
        """Return the definitions needed for *name*, dependencies first.

        Raises :class:`~pcbkit.errors.SymbolCycleError` if a circular reference is
        found, instead of recursing forever.
        """
        ordered: list[SymbolDefinition] = []
        emitted: set[str] = set()
        stack: list[str] = []

        def visit(current: str) -> None:
            key = current.casefold()
            if key in emitted:
                return
            if key in stack:
                raise SymbolCycleError(stack[stack.index(key):] + [current])

            definition = self.definition(current)
            if definition is None:
                return

            stack.append(key)
            for child in self.custom_references(definition.features):
                visit(child)
            stack.pop()

            emitted.add(key)
            ordered.append(definition)

        visit(name)
        return ordered

    def custom_references(self, features: FeatureFile, unit: UnitSystem = UnitSystem.MM) -> list[str]:
        """Distinct user-defined symbol names referenced by *features*.

        Only names that do **not** parse as standard symbols are returned, which
        is what makes the recursion terminate on real data.
        """
        out: list[str] = []
        for ref in features.symbols:
            if parse_standard_symbol(ref.name, ref.effective_unit(unit)) is None:
                if ref.name not in out:
                    out.append(ref.name)
        return out

    def check_cycles(self, roots: list[str] | None = None) -> list[str]:
        """Validate all symbols reachable from *roots* (default: all of them).

        Returns the names that were checked.  Raises on the first cycle found.
        """
        names = roots if roots is not None else self.names
        for name in names:
            self.expand_order(name)
        return names
