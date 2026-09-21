"""Parser for ``features`` / ``profile`` files (Line Record Text).

Spec: p.164-180 (the ``features`` file), p.138 (step profile), p.181 (layer
profile), p.184 (tools).

The grammar is *positional but partially optional*, which is the main
implementation hazard:

    P <x> <y> <apt_def> <polarity> <dcode> <orient_def>;<atr>=<value>,...;ID=<id>
    L <xs> <ys> <xe> <ye> <sym_num> <polarity> <dcode>;<atr>=<value>,...;ID=<id>
    A <xs> <ys> <xe> <ye> <xc> <yc> <sym_num> <polarity> <dcode> <cw>;...;ID=<id>
    T <x> <y> <font> <polarity> <orient_def> <xsize> <ysize> <wf> <text> <ver>;...;ID=<id>
    S <polarity> <dcode>;<atr>=<value>,...;ID=<id>

Two shapes must be special-cased rather than counted, because they expand the
position list by a variable number of tokens:

``apt_def``
    Either ``<sym_num>`` or ``-1 <sym_num> <resize_factor>`` (spec p.170).

``orient_def``
    Either a legacy digit ``0``-``7`` or ``8``/``9`` *followed by an angle*
    (spec p.170-171).

Consequently the record parsers walk from the front with explicit cursor
advance instead of slicing a fixed number of fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from .archive import OdbArchive
from .diagnostics import (
    FEATURES_BAD_ATTRIBUTE_REF,
    FEATURES_COUNT_MISMATCH,
    FEATURES_MULTIPLE_SURFACES,
    FEATURES_NO_UNITS,
    FEATURES_UNDECLARED_ATTRIBUTE,
    FEATURES_UNKNOWN_RECORD,
    FEATURES_UNSUPPORTED_RECORD,
    Diagnostics,
)
from .errors import ParseError
from .model import (
    ContourPart,
    Orientation,
    Point,
    Polarity,
    Polygon,
    SurfaceGeometry,
)
from .textio import Token, iter_content_lines, split_sections, tokenize
from .units import UnitScope, UnitSystem

__all__ = [
    "SymbolRef",
    "Feature",
    "PadFeature",
    "LineFeature",
    "ArcFeature",
    "SurfaceFeature",
    "FeatureFile",
    "parse_feature_file",
    "parse_profile",
    "resolve_attributes",
    "AttributeValue",
]

AttributeValue = Union[str, int, float, bool]


# ---------------------------------------------------------------------------
# symbol table entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolRef:
    """A ``$<n> <name> [I|M]`` entry of the feature symbol names section.

    ``unit`` is the *explicit* per-symbol override from the trailing ``I``/``M``
    marker (spec p.167); ``None`` means "use the file's units".  This marker is
    why unit handling cannot be decided once per file.
    """

    index: int
    name: str
    unit: Optional[UnitSystem] = None

    def effective_unit(self, file_unit: UnitSystem) -> UnitSystem:
        return self.unit if self.unit is not None else file_unit

    @property
    def has_unit_override(self) -> bool:
        return self.unit is not None


# ---------------------------------------------------------------------------
# feature records
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    """Fields common to every feature record.

    ``attributes`` keys are attribute *names*; values are typed according to the
    job's attribute definitions (see :func:`resolve_attributes`).
    """

    line_no: int
    polarity: Polarity = Polarity.POSITIVE
    dcode: int = 0
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    uid: Optional[int] = None
    raw: str = ""

    @property
    def kind(self) -> str:
        raise NotImplementedError


@dataclass
class PadFeature(Feature):
    """``P`` -- a symbol placed at a point (spec p.170)."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    symbol: Optional[SymbolRef] = None
    #: ``apt_def = -1 <sym> <resize>``.  Stored raw in micrometre/mil units;
    #: see :attr:`resize_mm` for the converted value.
    resize_raw: Optional[float] = None
    resize_unit: Optional[UnitSystem] = None
    orientation: Orientation = field(default_factory=Orientation)

    @property
    def kind(self) -> str:
        return "pad"

    @property
    def resize_mm(self) -> Optional[float]:
        """Resize delta in millimetres, or ``None`` when not resized.

        .. warning::
           The spec (p.170) calls this a "factor" expressed in
           "thousandths of the units being used (mils or microns)" while its own
           example (p.172) describes ``0.02`` as "resized by 0.02 mil".  The two
           readings disagree by 1000x.  We implement the example's reading --
           the value is in micrometre/mil, applied as a dimension delta -- and
           flag it here because **no case in ``cases/`` exercises this field**,
           so the choice is unverified against real data.
        """
        if self.resize_raw is None:
            return None
        unit = self.resize_unit or UnitSystem.MM
        return UnitScope(unit).size_mm(self.resize_raw)


@dataclass
class LineFeature(Feature):
    """``L`` -- a symbol swept along a straight segment (spec p.169)."""

    start_mm: Point = (0.0, 0.0)
    end_mm: Point = (0.0, 0.0)
    symbol: Optional[SymbolRef] = None

    @property
    def kind(self) -> str:
        return "line"

    @property
    def length_mm(self) -> float:
        (x0, y0), (x1, y1) = self.start_mm, self.end_mm
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


@dataclass
class ArcFeature(Feature):
    """``A`` -- a symbol swept along a circular segment (spec p.172).

    Start == end means a 360-degree arc; single-point arcs do not exist.
    """

    start_mm: Point = (0.0, 0.0)
    end_mm: Point = (0.0, 0.0)
    center_mm: Point = (0.0, 0.0)
    clockwise: bool = True
    symbol: Optional[SymbolRef] = None

    @property
    def kind(self) -> str:
        return "arc"

    @property
    def is_full_circle(self) -> bool:
        return self.start_mm == self.end_mm

    @property
    def radius_mm(self) -> float:
        (x0, y0), (cx, cy) = self.start_mm, self.center_mm
        return ((x0 - cx) ** 2 + (y0 - cy) ** 2) ** 0.5


@dataclass
class SurfaceFeature(Feature):
    """``S ... SE`` -- one or more closed boundaries (spec p.178)."""

    geometry: SurfaceGeometry = field(default_factory=SurfaceGeometry)

    @property
    def kind(self) -> str:
        return "surface"

    @property
    def polygons(self) -> list[Polygon]:
        return self.geometry.polygons


# ---------------------------------------------------------------------------
# parsed file
# ---------------------------------------------------------------------------

@dataclass
class FeatureFile:
    """A parsed ``features`` file."""

    path: str
    units: UnitScope
    declared_units: Optional[UnitSystem] = None
    uid: Optional[int] = None
    declared_count: Optional[int] = None
    symbols: list[SymbolRef] = field(default_factory=list)
    attr_names: list[str] = field(default_factory=list)
    attr_strings: list[str] = field(default_factory=list)
    records: list[Feature] = field(default_factory=list)

    @property
    def actual_count(self) -> int:
        return len(self.records)

    def symbol(self, index: int) -> Optional[SymbolRef]:
        """Symbol by **serial number**; the ``$`` serials are looked up by index."""
        if 0 <= index < len(self.symbols):
            return self.symbols[index]
        return None

    def counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rec in self.records:
            out[rec.kind] = out.get(rec.kind, 0) + 1
        return out

    def surface_geometry(self) -> SurfaceGeometry:
        """Merge every surface record into one geometry container."""
        merged = SurfaceGeometry()
        for rec in self.records:
            if isinstance(rec, SurfaceFeature):
                merged.polygons.extend(rec.geometry.polygons)
        return merged

    def custom_symbol_names(self, is_standard) -> list[str]:
        """Distinct referenced symbol names for which *is_standard* is false."""
        seen: list[str] = []
        for ref in self.symbols:
            if not is_standard(ref.name) and ref.name not in seen:
                seen.append(ref.name)
        return seen


# ---------------------------------------------------------------------------
# attribute resolution
# ---------------------------------------------------------------------------

def resolve_attributes(
    assignments: "dict[int, Optional[str]]",
    attr_names: list[str],
    attr_strings: list[str],
    definitions=None,
    *,
    path: Optional[str] = None,
    diagnostics: Optional[Diagnostics] = None,
) -> tuple[dict[str, AttributeValue], list[str]]:
    """Resolve ``<index>=<value>`` pairs into ``{attribute_name: value}``.

    The indirection is two-level (spec p.168): the record stores an index into
    the ``@`` name table, and for a ``TEXT`` attribute the stored value is
    itself an index into the ``&`` string table.  Interpreting the second number
    therefore requires knowing the declared type of the attribute.

    Returns ``(resolved, undeclared_names)``.  When a definition is unavailable
    the raw token is preserved and the name is reported: guessing (for example
    "the value is small, so it must be a string index") silently corrupts data.
    """
    resolved: dict[str, AttributeValue] = {}
    undeclared: list[str] = []

    for index, raw_value in assignments.items():
        if index >= len(attr_names):
            if diagnostics is not None:
                diagnostics.warn(
                    FEATURES_BAD_ATTRIBUTE_REF,
                    f"attribute index {index} is outside the attribute name table "
                    f"(size {len(attr_names)})",
                    path,
                )
            continue

        name = attr_names[index]
        decl = definitions.get(name) if definitions is not None else None
        if decl is None:
            undeclared.append(name)
        resolved[name] = _coerce(name, raw_value, decl, attr_strings, path, diagnostics)

    return resolved, undeclared


def _coerce(
    name: str,
    raw: Optional[str],
    decl,
    attr_strings: list[str],
    path: Optional[str],
    diagnostics: Optional[Diagnostics],
) -> AttributeValue:
    kind = (decl.type or "").upper() if decl is not None else ""

    if raw is None:
        # The index was listed with no value: the boolean is TRUE (spec p.178).
        return True

    if kind == "BOOLEAN":
        return True

    if kind == "TEXT":
        try:
            idx = int(raw)
        except ValueError:
            return raw
        if 0 <= idx < len(attr_strings):
            return attr_strings[idx]
        if diagnostics is not None:
            diagnostics.warn(
                FEATURES_BAD_ATTRIBUTE_REF,
                f"attribute {name!r} references text string {idx} but the string table "
                f"has {len(attr_strings)} entries",
                path,
            )
        return raw

    if kind in ("INTEGER", "OPTION"):
        try:
            return int(raw)
        except ValueError:
            return raw

    if kind in ("FLOAT", "REAL"):
        try:
            return float(raw)
        except ValueError:
            return raw

    # Unknown or undeclared: keep the token exactly as written.
    return raw


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def _split_record(
    line: str,
    path: Optional[str],
    line_no: int,
) -> tuple[list[Token], "dict[int, Optional[str]]", Optional[int]]:
    """Split a record into ``(field tokens, attribute assignments, uid)``.

    The leading record-type token (``P``/``L``/``A``/``T``/``B``/``S``) is
    consumed, so field tokens start at the first positional argument.  Every
    record parser therefore indexes from 0 for its first *field*, which keeps the
    parsers readable against the spec's parameter tables.

    The attribute suffix is a comma separated list of ``<atr>=<value>`` items, but
    a **bare index** is also legal and means "this boolean attribute is TRUE"
    (spec p.178: *"BOOLEAN - No value is necessary.  If the index of the
    attribute is listed, the attribute is set to TRUE"*).  Real files use both
    forms in one list, e.g. ``;0=0,1``, so a bare index is recorded as ``None``
    rather than being rejected or silently treated as a name.
    """
    sections = split_sections(line)
    tokens = tokenize(sections[0], path=path, line_no=line_no)

    if not tokens:
        raise ParseError("empty record", path=path, line_no=line_no, line=line)
    tokens = tokens[1:]

    assignments: dict[int, Optional[str]] = {}
    uid: Optional[int] = None

    for section in sections[1:]:
        section = section.strip()
        if not section:
            # Empty sections are normal and legal: "S P 0;;ID=138" (spec p.138).
            continue
        for item in section.split(","):
            item = item.strip()
            if not item:
                continue
            if item.upper().startswith("ID="):
                try:
                    uid = int(item[3:].strip())
                except ValueError:
                    raise ParseError(
                        f"malformed feature ID: {item!r}",
                        path=path, line_no=line_no, line=line,
                    ) from None
                continue
            key, sep, value = item.partition("=")
            if not sep:
                # Bare index: a boolean attribute that is simply present.
                try:
                    assignments[int(key.strip())] = None
                except ValueError:
                    raise ParseError(
                        f"malformed attribute assignment: {item!r}",
                        path=path, line_no=line_no, line=line,
                    ) from None
                continue
            try:
                assignments[int(key.strip())] = value.strip()
            except ValueError:
                raise ParseError(
                    f"attribute index is not an integer: {key!r}",
                    path=path, line_no=line_no, line=line,
                ) from None

    return tokens, assignments, uid


def _num(token: Token, *, path: Optional[str], line_no: int, line: str, what: str) -> float:
    try:
        return float(token.text)
    except ValueError:
        raise ParseError(
            f"expected a number for {what}, got {token.text!r}",
            path=path, line_no=line_no, line=line,
        ) from None


def _int(token: Token, *, path: Optional[str], line_no: int, line: str, what: str) -> int:
    try:
        return int(token.text)
    except ValueError:
        try:
            return int(float(token.text))
        except ValueError:
            raise ParseError(
                f"expected an integer for {what}, got {token.text!r}",
                path=path, line_no=line_no, line=line,
            ) from None


def _polarity(token: Token, *, path: Optional[str], line_no: int, line: str) -> Polarity:
    value = token.text.upper()
    if value == "P":
        return Polarity.POSITIVE
    if value == "N":
        return Polarity.NEGATIVE
    raise ParseError(
        f"expected polarity P or N, got {token.text!r}",
        path=path, line_no=line_no, line=line,
    )


def _yn(token: Token) -> bool:
    return token.text.strip().upper().startswith("Y")


def _parse_orientation(
    tokens: list[Token],
    pos: int,
    *,
    path: Optional[str],
    line_no: int,
    line: str,
) -> tuple[Orientation, int]:
    """Parse ``orient_def`` at *pos*; return ``(orientation, tokens consumed)``.

    A non-digit value is tolerated as "no transform" so that one odd record
    cannot abort an entire layer.
    """
    if pos >= len(tokens):
        raise ParseError("missing orientation definition", path=path, line_no=line_no, line=line)

    raw = tokens[pos].text
    if not raw.isdigit():
        return Orientation(), 1

    code = int(raw)
    if code <= 7:
        return Orientation.from_legacy_code(code), 1

    if code in (8, 9):
        if pos + 1 >= len(tokens):
            raise ParseError(
                f"orientation {code} must be followed by a rotation angle",
                path=path, line_no=line_no, line=line,
            )
        angle = _num(tokens[pos + 1], path=path, line_no=line_no, line=line,
                     what="orientation rotation")
        return Orientation.from_angle(angle, mirrored=(code == 9), code=code), 2

    raise ParseError(
        f"invalid orientation definition {code} (expected 0-9)",
        path=path, line_no=line_no, line=line,
    )


def _symbol_at(symbols: list[SymbolRef], index: int) -> Optional[SymbolRef]:
    if 0 <= index < len(symbols):
        return symbols[index]
    return None


# ---------------------------------------------------------------------------
# record parsers
# ---------------------------------------------------------------------------

def _parse_pad(
    tokens: list[Token], line: str, line_no: int, path: Optional[str],
    scope: UnitScope, symbols: list[SymbolRef], uid: Optional[int],
    definitions, assignments: "dict[int, Optional[str]]", attr_names: list[str],
    attr_strings: list[str], diagnostics: Diagnostics,
) -> PadFeature:
    if len(tokens) < 5:
        raise ParseError("pad record needs at least x y apt_def polarity dcode",
                         path=path, line_no=line_no, line=line)

    x = scope.coord_mm(_num(tokens[0], path=path, line_no=line_no, line=line, what="x"))
    y = scope.coord_mm(_num(tokens[1], path=path, line_no=line_no, line=line, what="y"))

    # apt_def: <sym_num> | -1 <sym_num> <resize_factor>
    pos = 2
    resize_raw: Optional[float] = None
    sym_index = _int(tokens[pos], path=path, line_no=line_no, line=line, what="apt_def")
    pos += 1
    if sym_index == -1:
        if len(tokens) < pos + 2:
            raise ParseError(
                "apt_def '-1' must be followed by a symbol index and a resize factor",
                path=path, line_no=line_no, line=line,
            )
        sym_index = _int(tokens[pos], path=path, line_no=line_no, line=line,
                         what="apt_def symbol index")
        pos += 1
        resize_raw = _num(tokens[pos], path=path, line_no=line_no, line=line,
                          what="apt_def resize factor")
        pos += 1

    polarity = _polarity(tokens[pos], path=path, line_no=line_no, line=line)
    pos += 1

    dcode = _int(tokens[pos], path=path, line_no=line_no, line=line, what="dcode") \
        if pos < len(tokens) else 0
    pos += 1

    orientation, _ = _parse_orientation(tokens, pos, path=path, line_no=line_no, line=line)

    attrs, undeclared = resolve_attributes(assignments, attr_names, attr_strings,
                                          definitions, path=path, diagnostics=diagnostics)
    _report_undeclared(undeclared, path, diagnostics)

    return PadFeature(
        line_no=line_no,
        polarity=polarity,
        dcode=dcode,
        attributes=attrs,
        uid=uid,
        raw=line,
        x_mm=x,
        y_mm=y,
        symbol=_symbol_at(symbols, sym_index),
        resize_raw=resize_raw,
        # The resize factor follows the *size* unit semantic.
        resize_unit=scope.system,
        orientation=orientation,
    )


def _parse_line(
    tokens: list[Token], line: str, line_no: int, path: Optional[str],
    scope: UnitScope, symbols: list[SymbolRef], uid: Optional[int],
    definitions, assignments: "dict[int, Optional[str]]", attr_names: list[str],
    attr_strings: list[str], diagnostics: Diagnostics,
) -> LineFeature:
    if len(tokens) < 6:
        raise ParseError("line record needs at least xs ys xe ye sym_num polarity",
                         path=path, line_no=line_no, line=line)

    values = [_num(tokens[i], path=path, line_no=line_no, line=line, what=f"field {i}")
              for i in range(4)]
    sym_index = _int(tokens[4], path=path, line_no=line_no, line=line, what="sym_num")
    polarity = _polarity(tokens[5], path=path, line_no=line_no, line=line)
    dcode = _int(tokens[6], path=path, line_no=line_no, line=line, what="dcode") \
        if len(tokens) > 6 else 0

    attrs, undeclared = resolve_attributes(assignments, attr_names, attr_strings,
                                          definitions, path=path, diagnostics=diagnostics)
    _report_undeclared(undeclared, path, diagnostics)

    return LineFeature(
        line_no=line_no,
        polarity=polarity,
        dcode=dcode,
        attributes=attrs,
        uid=uid,
        raw=line,
        start_mm=(scope.coord_mm(values[0]), scope.coord_mm(values[1])),
        end_mm=(scope.coord_mm(values[2]), scope.coord_mm(values[3])),
        symbol=_symbol_at(symbols, sym_index),
    )


def _parse_arc(
    tokens: list[Token], line: str, line_no: int, path: Optional[str],
    scope: UnitScope, symbols: list[SymbolRef], uid: Optional[int],
    definitions, assignments: "dict[int, Optional[str]]", attr_names: list[str],
    attr_strings: list[str], diagnostics: Diagnostics,
) -> ArcFeature:
    if len(tokens) < 8:
        raise ParseError(
            "arc record needs xs ys xe ye xc yc sym_num polarity [dcode] [cw]",
            path=path, line_no=line_no, line=line,
        )

    values = [_num(tokens[i], path=path, line_no=line_no, line=line, what=f"field {i}")
              for i in range(6)]
    sym_index = _int(tokens[6], path=path, line_no=line_no, line=line, what="sym_num")
    polarity = _polarity(tokens[7], path=path, line_no=line_no, line=line)

    # Both dcode and cw are optional and either may be absent.
    dcode = 0
    clockwise = True
    for token in tokens[8:]:
        text = token.text.strip()
        if text.upper() in ("Y", "N"):
            clockwise = text.upper() == "Y"
        else:
            try:
                dcode = int(float(text))
            except ValueError:
                pass

    attrs, undeclared = resolve_attributes(assignments, attr_names, attr_strings,
                                          definitions, path=path, diagnostics=diagnostics)
    _report_undeclared(undeclared, path, diagnostics)

    return ArcFeature(
        line_no=line_no,
        polarity=polarity,
        dcode=dcode,
        attributes=attrs,
        uid=uid,
        raw=line,
        start_mm=(scope.coord_mm(values[0]), scope.coord_mm(values[1])),
        end_mm=(scope.coord_mm(values[2]), scope.coord_mm(values[3])),
        center_mm=(scope.coord_mm(values[4]), scope.coord_mm(values[5])),
        clockwise=clockwise,
        symbol=_symbol_at(symbols, sym_index),
    )


def _report_undeclared(names: list[str], path: Optional[str],
                       diagnostics: Diagnostics) -> None:
    """Report undeclared attributes once per file rather than once per record."""
    if not names:
        return
    unique = sorted(set(names))
    diagnostics.warn(
        FEATURES_UNDECLARED_ATTRIBUTE,
        f"{len(unique)} attribute(s) have no definition and keep their raw token: "
        + ", ".join(unique[:8]) + ("..." if len(unique) > 8 else ""),
        path,
    )


# ---------------------------------------------------------------------------
# file parser
# ---------------------------------------------------------------------------

_OB = re.compile(r"^OB(?:\s|$)")
_OS = re.compile(r"^OS(?:\s|$)")
_OC = re.compile(r"^OC(?:\s|$)")
_OE = re.compile(r"^OE\s*$")
_SE = re.compile(r"^SE\s*$")
_RECORD = re.compile(r"^([PLATBS])(?:\s|$)")

_RECORD_PARSERS = {
    "P": _parse_pad,
    "L": _parse_line,
    "A": _parse_arc,
}

#: Record types this parser recognises but deliberately does not implement.
#: They are named explicitly rather than falling through to
#: ``FEATURES_UNKNOWN_RECORD``, because "unknown" would suggest a parser bug
#: while this is a scope decision -- and because a silent skip would drop real
#: geometry from the layer without a trace.
_UNSUPPORTED_RECORDS = {
    "T": "text",
    "B": "barcode",
}


def parse_feature_file(
    archive: OdbArchive,
    relpath: str,
    *,
    default_scope: UnitScope,
    diagnostics: Diagnostics,
    definitions=None,
    require_units: bool = False,
) -> FeatureFile:
    """Parse a ``features`` (or ``profile``) file.

    ``default_scope`` is the product model's unit scope, used when the file has
    no ``UNITS`` directive (spec p.23: the model default applies, and if that is
    absent too, imperial).
    """
    text = archive.read_text(relpath)

    result = FeatureFile(path=relpath, units=default_scope)

    # @ / & tables are indexed explicitly, so they are collected by index and
    # materialised as dense lists.  ODB++ permits gaps in the indices, and the
    # tables must be *live* while records are parsed because a record resolves
    # its attribute index against them as soon as it is read.
    attr_names_by_index: dict[int, str] = {}
    attr_strings_by_index: dict[int, str] = {}

    current_surface: Optional[SurfaceFeature] = None
    current_polygon: Optional[Polygon] = None
    polygon_parts: list[ContourPart] = []

    for line_no, line in iter_content_lines(text):
        # --- directives and tables -----------------------------------------
        if line.startswith("UNITS="):
            system = UnitSystem.parse(line.split("=", 1)[1], path=relpath, line_no=line_no)
            result.declared_units = system
            result.units = UnitScope(system, relpath)
            continue

        if line.startswith("ID="):
            try:
                result.uid = int(line.split("=", 1)[1].strip())
            except ValueError:
                raise ParseError(f"malformed ID: {line!r}",
                                 path=relpath, line_no=line_no, line=line) from None
            continue

        if line == "F" or line.startswith("F "):
            try:
                result.declared_count = int(line.split(None, 1)[1].strip())
            except (IndexError, ValueError):
                raise ParseError(f"malformed feature count: {line!r}",
                                 path=relpath, line_no=line_no, line=line) from None
            continue

        if line.startswith("$"):
            result.symbols.append(_parse_symbol_line(line, relpath, line_no))
            continue

        if line.startswith("@"):
            index, name = _parse_table_entry(line, relpath, line_no, what="attribute name")
            attr_names_by_index[index] = name
            result.attr_names = _dense(attr_names_by_index)
            continue

        if line.startswith("&"):
            index, value = _parse_table_entry(line, relpath, line_no,
                                              what="attribute text string")
            attr_strings_by_index[index] = value
            result.attr_strings = _dense(attr_strings_by_index)
            continue

        # --- surface body ---------------------------------------------------
        if _OB.match(line):
            if current_surface is None:
                raise ParseError("OB outside of a surface",
                                 path=relpath, line_no=line_no, line=line)
            if current_polygon is not None:
                raise ParseError("nested OB: previous polygon was never closed",
                                 path=relpath, line_no=line_no, line=line)
            tokens = tokenize(line, path=relpath, line_no=line_no)
            if len(tokens) < 4:
                raise ParseError("OB needs x y poly_type",
                                 path=relpath, line_no=line_no, line=line)
            kind = tokens[3].text.upper()
            if kind not in ("I", "H"):
                raise ParseError(
                    f"OB polygon type must be I (island) or H (hole), got {kind!r}",
                    path=relpath, line_no=line_no, line=line,
                )
            current_polygon = Polygon(
                is_island=(kind == "I"),
                start=(
                    result.units.coord_mm(_num(tokens[1], path=relpath, line_no=line_no,
                                               line=line, what="OB x")),
                    result.units.coord_mm(_num(tokens[2], path=relpath, line_no=line_no,
                                               line=line, what="OB y")),
                ),
            )
            polygon_parts = []
            continue

        if _OS.match(line):
            if current_polygon is None:
                raise ParseError("OS outside of a polygon",
                                 path=relpath, line_no=line_no, line=line)
            tokens = tokenize(line, path=relpath, line_no=line_no)
            if len(tokens) < 3:
                raise ParseError("OS needs x y", path=relpath, line_no=line_no, line=line)
            polygon_parts.append(ContourPart(
                kind="segment",
                end=(
                    result.units.coord_mm(_num(tokens[1], path=relpath, line_no=line_no,
                                               line=line, what="OS x")),
                    result.units.coord_mm(_num(tokens[2], path=relpath, line_no=line_no,
                                               line=line, what="OS y")),
                ),
            ))
            continue

        if _OC.match(line):
            if current_polygon is None:
                raise ParseError("OC outside of a polygon",
                                 path=relpath, line_no=line_no, line=line)
            tokens = tokenize(line, path=relpath, line_no=line_no)
            if len(tokens) < 6:
                raise ParseError("OC needs xe ye xc yc cw",
                                 path=relpath, line_no=line_no, line=line)
            clockwise = True
            for token in tokens[5:]:
                if token.text.upper() in ("Y", "N"):
                    clockwise = token.text.upper() == "Y"
            polygon_parts.append(ContourPart(
                kind="arc",
                end=(
                    result.units.coord_mm(_num(tokens[1], path=relpath, line_no=line_no,
                                               line=line, what="OC xe")),
                    result.units.coord_mm(_num(tokens[2], path=relpath, line_no=line_no,
                                               line=line, what="OC ye")),
                ),
                center=(
                    result.units.coord_mm(_num(tokens[3], path=relpath, line_no=line_no,
                                               line=line, what="OC xc")),
                    result.units.coord_mm(_num(tokens[4], path=relpath, line_no=line_no,
                                               line=line, what="OC yc")),
                ),
                clockwise=clockwise,
            ))
            continue

        if _OE.match(line):
            if current_polygon is None:
                raise ParseError("OE without a matching OB",
                                 path=relpath, line_no=line_no, line=line)
            current_polygon.parts = polygon_parts
            assert current_surface is not None
            current_surface.geometry.polygons.append(current_polygon)
            current_polygon = None
            polygon_parts = []
            continue

        if _SE.match(line):
            if current_polygon is not None:
                raise ParseError("surface closed while a polygon was still open",
                                 path=relpath, line_no=line_no, line=line)
            current_surface = None
            continue

        # --- feature records -------------------------------------------------
        match = _RECORD.match(line)
        if match:
            kind = match.group(1)
            tokens, assignments, uid = _split_record(line, relpath, line_no)

            if kind == "S":
                if len(tokens) < 1:
                    raise ParseError("surface record needs a polarity",
                                     path=relpath, line_no=line_no, line=line)
                attrs, undeclared = resolve_attributes(
                    assignments, result.attr_names, result.attr_strings, definitions,
                    path=relpath, diagnostics=diagnostics,
                )
                _report_undeclared(undeclared, relpath, diagnostics)
                surface = SurfaceFeature(
                    line_no=line_no,
                    polarity=_polarity(tokens[0], path=relpath, line_no=line_no, line=line),
                    dcode=_int(tokens[1], path=relpath, line_no=line_no, line=line,
                               what="dcode") if len(tokens) > 1 else 0,
                    attributes=attrs,
                    uid=uid,
                    raw=line,
                )
                result.records.append(surface)
                current_surface = surface
                continue

            # T (text) and B (barcode) are out of scope: they need a vector font
            # or a symbology renderer, not geometry expansion.  They are reported
            # by name so a layer silently missing its silkscreen text is visible.
            if kind in _UNSUPPORTED_RECORDS:
                diagnostics.warn(
                    FEATURES_UNSUPPORTED_RECORD,
                    f"{kind} ({_UNSUPPORTED_RECORDS[kind]}) records are outside this "
                    f"parser's scope; the record is ignored",
                    relpath,
                )
                continue

            common = dict(
                line=line, line_no=line_no, path=relpath, scope=result.units,
                uid=uid, definitions=definitions,
                assignments=assignments, attr_names=result.attr_names,
                attr_strings=result.attr_strings, diagnostics=diagnostics,
            )
            record: Feature = _RECORD_PARSERS[kind](
                tokens, symbols=result.symbols, **common
            )

            result.records.append(record)
            continue

        diagnostics.warn(
            FEATURES_UNKNOWN_RECORD,
            f"unrecognised record {line.split()[0]!r}; ignored",
            relpath,
        )

    if current_polygon is not None or current_surface is not None:
        raise ParseError("file ended inside an unterminated surface", path=relpath)

    result.attr_names = _dense(attr_names_by_index)
    result.attr_strings = _dense(attr_strings_by_index)

    if result.declared_units is None and require_units:
        diagnostics.warn(
            FEATURES_NO_UNITS,
            "no UNITS directive; falling back to the product model default",
            relpath,
        )

    if result.declared_count is not None and result.declared_count != result.actual_count:
        diagnostics.warn(
            FEATURES_COUNT_MISMATCH,
            f"declared {result.declared_count} features but parsed {result.actual_count}",
            relpath,
        )

    return result


def _dense(by_index: dict[int, str]) -> list[str]:
    """Materialise an index->value table into a dense list, padding gaps."""
    if not by_index:
        return []
    return [by_index.get(i, "") for i in range(max(by_index) + 1)]


_TABLE_ENTRY = re.compile(r"^([@&])(\d+)(?:\s+(.*))?$", re.DOTALL)


def _parse_table_entry(
    line: str, path: str, line_no: int, *, what: str
) -> tuple[int, str]:
    """Parse an ``@<n> <name>`` or ``&<n> <string>`` table row.

    ``&0`` with nothing after it is legal (an empty string), hence the optional
    tail in the pattern.

    Note: leading/trailing whitespace inside ``&`` values cannot be preserved
    because the line reader strips lines; ODB++ attribute strings with
    significant surrounding whitespace are pathological and are not produced by
    any CAM tool we have seen.
    """
    match = _TABLE_ENTRY.match(line)
    if match is None:
        raise ParseError(f"malformed {what} row: {line!r}",
                         path=path, line_no=line_no, line=line)
    return int(match.group(2)), line[match.start(3):] if match.group(3) is not None else ""


def _parse_symbol_line(line: str, path: str, line_no: int) -> SymbolRef:
    """Parse ``$<n> <name> [I|M]``.

    The serial number is assigned by the file, so the list is indexed by serial
    and gaps are permitted.
    """
    tokens = tokenize(line, path=path, line_no=line_no)
    serial = tokens[0].text.lstrip("$")
    try:
        index = int(serial)
    except ValueError:
        raise ParseError(f"malformed symbol serial number: {line!r}",
                         path=path, line_no=line_no, line=line) from None

    if len(tokens) < 2:
        raise ParseError(f"symbol line without a name: {line!r}",
                         path=path, line_no=line_no, line=line)

    unit: Optional[UnitSystem] = None
    if len(tokens) >= 3:
        marker = tokens[2].text.strip().upper()
        if marker in ("I", "M"):
            unit = UnitSystem.INCH if marker == "I" else UnitSystem.MM

    return SymbolRef(index=index, name=tokens[1].text, unit=unit)


def parse_profile(
    archive: OdbArchive,
    relpath: str,
    *,
    default_scope: UnitScope,
    diagnostics: Diagnostics,
    definitions=None,
) -> SurfaceGeometry:
    """Parse a step or layer ``profile`` file into a :class:`SurfaceGeometry`.

    A profile is exactly one surface feature (spec p.138, p.181).  More than one
    is reported rather than silently merged, because it changes the base-polygon
    semantics that AGENTS constraint 3 relies on.
    """
    parsed = parse_feature_file(
        archive, relpath, default_scope=default_scope,
        diagnostics=diagnostics, definitions=definitions,
    )

    surfaces = [r for r in parsed.records if isinstance(r, SurfaceFeature)]
    if not surfaces:
        raise ParseError("profile contains no surface feature", path=relpath)
    if len(surfaces) > 1:
        diagnostics.warn(
            FEATURES_MULTIPLE_SURFACES,
            f"profile contains {len(surfaces)} surface features; the spec allows one",
            relpath,
        )

    geometry = SurfaceGeometry()
    for surface in surfaces:
        geometry.polygons.extend(surface.geometry.polygons)
    return geometry
