"""Data model for a parsed ODB++ job.

Deliberately two-layered:

*Raw* structures (:class:`FeatureFile`, :class:`Profile`, :class:`DrillTools`)
mirror what is literally in the files, including the declared-vs-actual feature
count and the ``@``/``&`` attribute tables.  Anything a downstream consumer or a
test might want to attribute back to the source lives here.

*Semantic* structures (:class:`Job`, :class:`Step`, :class:`Layer`) are the
resolved product model: layers in matrix order, each with its parsed geometry
and its resolved base polygon.

Units: every length field in the semantic layer is already in **millimetres**,
because AGENTS constraint 4 requires metric output.  Raw float values are kept
alongside where losing the original would make debugging painful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional

from .units import UnitScope

__all__ = [
    "Polarity",
    "LayerType",
    "LayerContext",
    "LayerForm",
    "DielectricType",
    "DrillToolType",
    "Orientation",
    "ProductInfo",
    "StepRef",
    "LayerRef",
    "Matrix",
    "StepRepeat",
    "StepHeader",
    "AttributeDef",
    "AttributeDefinitions",
    "DrillTool",
    "DrillTools",
    "ContourPart",
    "Polygon",
    "Point",
    "SurfaceGeometry",
    "Layer",
    "Step",
    "Job",
]

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# enumerations
# ---------------------------------------------------------------------------

class Polarity(str, Enum):
    """Record polarity: ``P`` covers, ``N`` erases (spec p.39)."""

    POSITIVE = "P"
    NEGATIVE = "N"

    @property
    def is_negative(self) -> bool:
        return self is Polarity.NEGATIVE


class LayerType(str, Enum):
    """Matrix ``TYPE`` values (spec p.65)."""

    SIGNAL = "SIGNAL"
    POWER_GROUND = "POWER_GROUND"
    DIELECTRIC = "DIELECTRIC"
    MIXED = "MIXED"
    SOLDER_MASK = "SOLDER_MASK"
    SOLDER_PASTE = "SOLDER_PASTE"
    SILK_SCREEN = "SILK_SCREEN"
    DRILL = "DRILL"
    ROUT = "ROUT"
    DOCUMENT = "DOCUMENT"
    COMPONENT = "COMPONENT"
    MASK = "MASK"
    CONDUCTIVE_PASTE = "CONDUCTIVE_PASTE"

    @property
    def is_copper(self) -> bool:
        """True for layers that carry copper geometry."""
        return self in (LayerType.SIGNAL, LayerType.POWER_GROUND, LayerType.MIXED)

    @property
    def is_drill(self) -> bool:
        return self in (LayerType.DRILL, LayerType.ROUT)

    @property
    def is_non_copper_material(self) -> bool:
        """Layers for which ``.layer_dielectric`` carries a material thickness.

        AGENTS note 4 / spec p.231: ``.layer_dielectric`` is meaningful for
        DIELECTRIC and SOLDER_MASK (material thickness) and SOLDER_PASTE
        (stencil thickness) -- never for copper.
        """
        return self in (LayerType.DIELECTRIC, LayerType.SOLDER_MASK,
                        LayerType.SOLDER_PASTE)


class LayerContext(str, Enum):
    BOARD = "BOARD"
    MISC = "MISC"


class LayerForm(str, Enum):
    """``FORM`` field: rigid or flexible laminate (spec p.66).

    Not a layer *type*: ``TYPE`` is the base type (``SIGNAL``, ``DIELECTRIC``,
    ...) and ``ADD_TYPE`` the subtype.  ``FORM`` is a separate optional field,
    and spec p.66 gives it a default, so there are only two values:

        RIGID -- Layer is defined as rigid.
        FLEX  -- Layer is defined as flexible.

    Resolution is in :data:`FLEX_SUBTYPES` / :class:`FormSource`, not here.
    """

    RIGID = "RIGID"
    FLEX = "FLEX"


#: Subtypes whose default ``FORM`` is ``FLEX`` rather than ``RIGID``.
#:
#: Spec p.66: "Layers of the subtype SIGNAL_FLEX, PG_FLEX and MIXED_FLEX have a
#: default FORM = FLEX."  That sentence is what makes a rigid-flex board
#: readable at all: ``cases/designodb-rigidflex`` never writes ``FORM``, and
#: these three subtypes are the only reason its flex region can be identified
#: from fields instead of from layer names.
FLEX_SUBTYPES = frozenset({"SIGNAL_FLEX", "PG_FLEX", "MIXED_FLEX"})


class FormSource(str, Enum):
    """Where the resolved :attr:`LayerRef.form` came from (spec p.66).

    Kept separate from the value so a consumer can tell "the exporter told us"
    from "we applied a default", without either of them having to be thrown
    away.  ``DEFAULT`` on a ``DIELECTRIC`` layer is also a spec deviation worth
    reporting, because that is precisely the layer where a wrong answer moves
    the flex boundary.
    """

    #: The ``LAYER`` block states ``FORM``.
    DECLARED = "DECLARED"
    #: Absent ``FORM``, ``ADD_TYPE`` is one of :data:`FLEX_SUBTYPES`.
    FLEX_SUBTYPE = "FLEX_SUBTYPE"
    #: Absent ``FORM`` and not a flex subtype; spec default ``RIGID`` applies,
    #: but note spec p.66: "Layers of type DIELECTRIC are to be defined as
    #: RIGID or FLEX" -- for those, silence is a deviation, not a default.
    DEFAULT = "DEFAULT"


class DielectricType(str, Enum):
    NONE = "NONE"
    PREPREG = "PREPREG"
    CORE = "CORE"


class DrillToolType(str, Enum):
    """``TOOLS/TYPE`` (spec: tools file)."""

    PLATED = "PLATED"
    NON_PLATED = "NON_PLATED"
    VIA = "VIA"

    @property
    def is_plated(self) -> bool:
        return self is not DrillToolType.NON_PLATED


# ---------------------------------------------------------------------------
# orientation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Orientation:
    """Pad/text/barcode orientation.

    ``orient_def`` is either a legacy single digit (``0``-``7``: 90-degree steps
    with a mirror flag) or a two-part ``8``/``9`` followed by an arbitrary angle
    (spec p.170-171).  Rotation is always clockwise and mirroring is always
    about the x axis; when both apply the rotation happens first (spec p.35).
    """

    rotation_deg: float = 0.0
    mirrored: bool = False
    legacy_code: Optional[int] = None

    @property
    def is_identity(self) -> bool:
        return self.rotation_deg == 0.0 and not self.mirrored

    @classmethod
    def from_legacy_code(cls, code: int) -> "Orientation":
        if not 0 <= code <= 7:
            raise ValueError(f"legacy orient_def must be 0..7, got {code}")
        return cls(
            rotation_deg=float((code % 4) * 90),
            mirrored=code >= 4,
            legacy_code=code,
        )

    @classmethod
    def from_angle(cls, degrees: float, *, mirrored: bool, code: int) -> "Orientation":
        from .units import UnitScope

        return cls(
            rotation_deg=UnitScope.normalize_angle(degrees),
            mirrored=mirrored,
            legacy_code=code,
        )


# ---------------------------------------------------------------------------
# misc/info
# ---------------------------------------------------------------------------

@dataclass
class ProductInfo:
    """Contents of ``misc/info`` (spec p.75)."""

    job_name: str = ""
    version_major: int = 0
    version_minor: int = 0
    source: str = ""
    creation_date: str = ""
    save_date: str = ""
    save_app: str = ""
    save_user: str = ""
    max_uid: Optional[int] = None
    #: Effective unit scope.  ``units_declared`` distinguishes an explicit
    #: ``UNITS`` directive from the imperial default (spec p.23).
    units: UnitScope = field(default_factory=UnitScope.default)
    units_declared: bool = False
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return f"{self.version_major}.{self.version_minor}"


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------

@dataclass
class StepRef:
    """A ``STEP`` array entry of the matrix (spec p.64)."""

    name: str
    col: int
    uid: Optional[int] = None


@dataclass
class LayerRef:
    """A ``LAYER`` array entry of the matrix (spec p.64-67)."""

    name: str
    row: int
    context: LayerContext = LayerContext.BOARD
    type: Optional[LayerType] = None
    raw_type: str = ""
    add_type: str = ""
    polarity: Polarity = Polarity.POSITIVE
    uid: Optional[int] = None
    form: LayerForm = LayerForm.RIGID
    #: Whether ``form`` was declared, implied by the subtype, or defaulted.
    form_source: FormSource = FormSource.DEFAULT
    dielectric_type: DielectricType = DielectricType.NONE
    dielectric_name: str = ""
    #: UIDs of the copper layers the dielectric is laminated to.
    cu_top: Optional[int] = None
    cu_bottom: Optional[int] = None
    #: UID of the copper layer a mask/paste/silk layer relates to.
    ref: Optional[int] = None
    old_name: str = ""
    #: Drill/rout span endpoints (layer *names*, not UIDs).
    start_name: str = ""
    end_name: str = ""
    color: str = ""

    @property
    def is_negative(self) -> bool:
        return self.polarity.is_negative

    @property
    def is_copper(self) -> bool:
        return self.type is not None and self.type.is_copper

    @property
    def is_flex(self) -> bool:
        """Resolved rigid/flex, per spec p.66's default rule.

        Always a definite answer, because the spec supplies one; use
        :attr:`form_source` when you need to know how firm it is.
        """
        return self.form is LayerForm.FLEX

    @property
    def form_is_declared(self) -> bool:
        """``True`` unless ``form`` came from the ``RIGID`` default."""
        return self.form_source is not FormSource.DEFAULT


@dataclass
class Matrix:
    """Parsed ``matrix/matrix`` -- the authoritative layer list."""

    steps: list[StepRef] = field(default_factory=list)
    layers: list[LayerRef] = field(default_factory=list)

    def step(self, name: str) -> Optional[StepRef]:
        folded = name.casefold()
        return next((s for s in self.steps if s.name.casefold() == folded), None)

    def layer(self, name: str) -> Optional[LayerRef]:
        folded = name.casefold()
        return next((l for l in self.layers if l.name.casefold() == folded), None)

    @property
    def board_layers(self) -> list[LayerRef]:
        """Rows with ``CONTEXT=BOARD``, in matrix row order."""
        return [l for l in self.layers if l.context is LayerContext.BOARD]

    @property
    def copper_layers(self) -> list[LayerRef]:
        """Copper stack in physical order (top to bottom by row)."""
        return [l for l in self.board_layers if l.is_copper]

    @property
    def dielectric_layers(self) -> list[LayerRef]:
        return [l for l in self.board_layers if l.type is LayerType.DIELECTRIC]

    @property
    def drill_layers(self) -> list[LayerRef]:
        return [l for l in self.board_layers if l.type in (LayerType.DRILL, LayerType.ROUT)]


# ---------------------------------------------------------------------------
# stephdr
# ---------------------------------------------------------------------------

@dataclass
class StepRepeat:
    """A nested step array entry.

    AGENTS constraint 1 excludes panel matrices and nested references, so these
    are recorded only so that the exclusion can be reported (spec p.139).
    """

    name: str = ""
    x: float = 0.0
    y: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    nx: int = 1
    ny: int = 1
    angle: float = 0.0
    mirrored: bool = False


@dataclass
class StepHeader:
    """Parsed ``steps/<step>/stephdr`` (spec p.139)."""

    units: UnitScope = field(default_factory=UnitScope.default)
    x_datum: float = 0.0
    y_datum: float = 0.0
    x_origin: float = 0.0
    y_origin: float = 0.0
    affecting_bom: str = ""
    uid: Optional[int] = None
    #: Non-empty means the job uses nested/mirrored step arrays -> unsupported.
    repeats: list[StepRepeat] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# attributes
# ---------------------------------------------------------------------------

@dataclass
class AttributeDef:
    """A ``misc/sysattr*`` or ``misc/userattr`` definition (spec p.84-90)."""

    name: str
    type: str = "TEXT"
    entity: str = ""
    cls: str = ""
    options: list[str] = field(default_factory=list)
    default: str = ""
    unit: str = ""
    source: str = ""

    @property
    def is_system(self) -> bool:
        return self.name.startswith(".")


@dataclass
class AttributeDefinitions:
    """All attribute definitions of a job, keyed by name (case-sensitive)."""

    defs: dict[str, AttributeDef] = field(default_factory=dict)

    def get(self, name: str) -> Optional[AttributeDef]:
        return self.defs.get(name)

    def type_of(self, name: str) -> Optional[str]:
        d = self.defs.get(name)
        return d.type if d else None

    def __contains__(self, name: str) -> bool:
        return name in self.defs

    def __len__(self) -> int:
        return len(self.defs)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

@dataclass
class DrillTool:
    """One ``TOOLS { }`` block of a drill/rout layer (spec p.184).

    ``finish_size_mm``/``drill_size_mm`` are converted with the **size**
    semantic (micrometre or mil), not the coordinate semantic.
    """

    num: int
    type: DrillToolType = DrillToolType.PLATED
    type2: str = "STANDARD"
    min_tol_mm: float = 0.0
    max_tol_mm: float = 0.0
    bit: str = ""
    finish_size_mm: float = 0.0
    drill_size_mm: float = 0.0
    raw_finish_size: float = 0.0
    raw_drill_size: float = 0.0
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class DrillTools:
    """Parsed ``steps/<step>/layers/<layer>/tools``."""

    units: UnitScope = field(default_factory=UnitScope.default)
    thickness_mm: float = 0.0
    tools: dict[int, DrillTool] = field(default_factory=dict)

    def get(self, num: int) -> Optional[DrillTool]:
        return self.tools.get(num)


# ---------------------------------------------------------------------------
# geometry containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContourPart:
    """One ``OS`` (segment) or ``OC`` (arc) step of a polygon boundary.

    Arc semantics are preserved rather than flattened at parse time, so a
    downstream consumer can choose its own discretisation tolerance.  ``OS``
    records also carry a ``cw`` field in the spec; it is meaningless for a
    straight segment and is dropped.
    """

    kind: str  # "segment" | "arc"
    end: Point
    center: Optional[Point] = None
    clockwise: Optional[bool] = None

    @property
    def is_arc(self) -> bool:
        return self.kind == "arc"


@dataclass
class Polygon:
    """A single closed boundary: an island or a hole (spec p.178-180).

    Orientation follows the spec convention: islands clockwise, holes
    counter-clockwise (p.39).  That convention is *validated* rather than
    enforced here, so malformed input can be diagnosed instead of silently
    repaired.
    """

    is_island: bool
    start: Point
    parts: list[ContourPart] = field(default_factory=list)

    def points(self) -> Iterator[Point]:
        """Yield the boundary vertices, including the closing point."""
        yield self.start
        for part in self.parts:
            yield part.end

    @property
    def arc_count(self) -> int:
        return sum(1 for p in self.parts if p.is_arc)


@dataclass
class SurfaceGeometry:
    """A surface: one or more islands, each with its holes (spec p.178)."""

    polygons: list[Polygon] = field(default_factory=list)

    @property
    def islands(self) -> list[Polygon]:
        return [p for p in self.polygons if p.is_island]

    @property
    def holes(self) -> list[Polygon]:
        return [p for p in self.polygons if not p.is_island]

    def __bool__(self) -> bool:
        return bool(self.polygons)


# ---------------------------------------------------------------------------
# semantic model
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    """A resolved layer: matrix metadata plus its parsed files."""

    ref: LayerRef
    #: Actual on-disk directory name, which may differ in case from
    #: ``ref.name`` (research 03 R1).
    dir_name: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    #: Unit scope of the layer's own ``attrlist`` (its ``UNITS`` when declared,
    #: else the job default).  Kept so attribute values keep their unit after
    #: the export normalises everything else to millimetres: ``.layer_dielectric``
    #: is 65 um in an INCH job and 0.0026 mm in an MM one.
    attribute_scope: Optional[UnitScope] = None
    #: Raw ``features`` file contents, or ``None`` when the layer has none.
    features: Optional[object] = None  # pcbkit.features.FeatureFile
    #: Layer-level profile.  Per AGENTS constraint 3 this takes priority over
    #: the step profile as the base polygon.
    profile: Optional[SurfaceGeometry] = None
    profile_error: Optional[str] = None
    tools: Optional[DrillTools] = None

    @property
    def name(self) -> str:
        return self.ref.name

    @property
    def type(self) -> Optional[LayerType]:
        return self.ref.type

    @property
    def is_negative(self) -> bool:
        """Layer polarity: features describe laminate rather than copper."""
        return self.ref.is_negative

    @property
    def is_copper(self) -> bool:
        return self.ref.is_copper

    @property
    def feature_count(self) -> int:
        return len(self.features.records) if self.features is not None else 0

    def tool_for(self, dcode: int) -> Optional[DrillTool]:
        if self.tools is None:
            return None
        return self.tools.tools.get(dcode)


@dataclass
class Step:
    """A single physical board step (AGENTS constraint 1: no panel arrays)."""

    name: str
    dir_name: str = ""
    header: Optional[StepHeader] = None
    profile: Optional[SurfaceGeometry] = None
    #: Layers in matrix row order (not directory order).
    layers: list[Layer] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    #: Unit scope of the step's own ``attrlist``.
    attribute_scope: Optional[UnitScope] = None

    def layer(self, name: str) -> Optional[Layer]:
        folded = name.casefold()
        return next((l for l in self.layers if l.name.casefold() == folded), None)

    @property
    def copper_layers(self) -> list[Layer]:
        return [l for l in self.layers if l.is_copper]

    @property
    def dielectric_layers(self) -> list[Layer]:
        return [l for l in self.layers if l.type is LayerType.DIELECTRIC]

    @property
    def drill_layers(self) -> list[Layer]:
        return [l for l in self.layers if l.type in (LayerType.DRILL, LayerType.ROUT)]

    @property
    def name_to_layer(self) -> dict[str, Layer]:
        return {l.name: l for l in self.layers}

    def base_geometry(self, layer: Layer) -> SurfaceGeometry:
        """Base polygon for *layer* per AGENTS constraint 3.

        Priority: the layer's own profile, else the step profile.  A layer
        profile that exists but could not be parsed raises
        :class:`~pcbkit.errors.LayerProfileError` -- silently falling back would
        fabricate geometry.

        The returned object is shared, not copied; callers must not mutate it.
        """
        from .errors import LayerProfileError

        if layer.profile_error is not None:
            raise LayerProfileError(layer.name, layer.profile_error)
        if layer.profile is not None:
            return layer.profile
        if self.profile is not None:
            return self.profile
        raise LayerProfileError(layer.name, "neither layer profile nor step profile is available")


@dataclass
class Job:
    """A fully loaded ODB++ job."""

    #: Directory (within the archive) that holds the product model.
    root: str = ""
    source_path: str = ""
    info: ProductInfo = field(default_factory=ProductInfo)
    matrix: Matrix = field(default_factory=Matrix)
    attributes: AttributeDefinitions = field(default_factory=AttributeDefinitions)
    steps: dict[str, Step] = field(default_factory=dict)
    #: Names of step directories present on disk but absent from the matrix.
    orphan_step_dirs: list[str] = field(default_factory=list)
    #: User-defined symbol library, preloaded with the symbols the loaded layers
    #: reference.  ``None`` when nothing referenced a user-defined symbol, or
    #: when loading was told to skip ``features``.
    symbols: Optional[object] = None  # pcbkit.symbols.SymbolLibrary
    diagnostics: object = None  # pcbkit.diagnostics.Diagnostics

    @property
    def units(self) -> UnitScope:
        return self.info.units

    def step(self, name: str) -> Optional[Step]:
        folded = name.casefold()
        return next((s for k, s in self.steps.items() if k.casefold() == folded), None)

    @property
    def single_step(self) -> Step:
        if len(self.steps) != 1:
            raise ValueError(f"job has {len(self.steps)} steps, not 1")
        return next(iter(self.steps.values()))
