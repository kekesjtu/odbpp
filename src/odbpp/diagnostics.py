"""Non-fatal problem collection.

Real ODB++ archives contain things that are legal-but-uninteresting (dangling
layer directories), things that are illegal-but-common (a ``features`` file
without ``UNITS``), and things this parser chooses not to support (nested step
arrays).  None of those should abort a parse, but all of them must be visible:
silently dropping data is how a PCB parser produces plausible-looking wrong
answers.

Every diagnostic carries a stable ``code`` so tests can assert on the reason
rather than on prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Literal, Optional

Level = Literal["warning", "error"]


@dataclass(frozen=True)
class Diagnostic:
    level: Level
    code: str
    message: str
    path: Optional[str] = None

    def __str__(self) -> str:
        loc = f" [{self.path}]" if self.path else ""
        return f"{self.level}: {self.code}: {self.message}{loc}"


@dataclass
class Diagnostics:
    """Ordered, de-duplicating collector of :class:`Diagnostic`."""

    items: list[Diagnostic] = field(default_factory=list)
    _seen: set[tuple[str, str, Optional[str]]] = field(default_factory=set, repr=False)

    def add(
        self,
        level: Level,
        code: str,
        message: str,
        path: Optional[str] = None,
    ) -> Diagnostic:
        key = (code, message, path)
        if key in self._seen:
            # Keep the first occurrence only: repeated per-layer warnings would
            # otherwise bury everything else.
            return Diagnostic(level, code, message, path)
        self._seen.add(key)
        diag = Diagnostic(level, code, message, path)
        self.items.append(diag)
        return diag

    def warn(self, code: str, message: str, path: Optional[str] = None) -> Diagnostic:
        return self.add("warning", code, message, path)

    def error(self, code: str, message: str, path: Optional[str] = None) -> Diagnostic:
        return self.add("error", code, message, path)

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.level == "warning"]

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.level == "error"]

    def codes(self) -> set[str]:
        return {d.code for d in self.items}

    def extend(self, other: Iterable[Diagnostic]) -> None:
        for d in other:
            self.add(d.level, d.code, d.message, d.path)

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __str__(self) -> str:
        return "\n".join(str(d) for d in self.items)


# --------------------------------------------------------------------------
# Diagnostic codes.
#
# Grouped by the research-doc section that motivates them, so a reader can go
# from a code straight to the reasoning.
# --------------------------------------------------------------------------

# Archive / job root
NO_JOB_ROOT = "archive.no_job_root"
MULTIPLE_JOB_ROOTS = "archive.multiple_job_roots"
CASE_INSENSITIVE_COLLISION = "archive.case_collision"

# misc/info & units
INFO_MISSING_UNITS = "info.missing_units"

# matrix (research 03 R2: matrix is the authoritative layer list)
LAYER_DIR_ORPHAN = "matrix.orphan_layer_dir"
LAYER_DIR_MISSING = "matrix.missing_layer_dir"
MATRIX_DUPLICATE_LAYER = "matrix.duplicate_layer"
MATRIX_UNKNOWN_LAYER_TYPE = "matrix.unknown_layer_type"
MATRIX_UNKNOWN_ADD_TYPE = "matrix.unknown_add_type"
MATRIX_DANGLING_CU_REF = "matrix.dangling_cu_ref"
MATRIX_UNKNOWN_FORM = "matrix.unknown_form"
#: A layer states FORM=RIGID *and* a subtype whose default is FLEX.
MATRIX_FORM_CONFLICT = "matrix.form_conflict"
#: Spec p.66: dielectric layers "are to be defined as RIGID or FLEX".
MATRIX_FORM_MISSING_DIELECTRIC = "matrix.form_missing_dielectric"

# attribute definitions (misc/sysattr*, misc/userattr)
ATTR_DUPLICATE_DEFINITION = "attr.duplicate_definition"
#: AGENTS note 5 / spec p.231: ``.layer_dielectric`` is a non-copper field.
ATTR_LAYER_DIELECTRIC_ON_COPPER = "attr.layer_dielectric_on_copper"

# features / profile records (research 01 §4.4)
FEATURES_NO_UNITS = "features.no_units"
FEATURES_COUNT_MISMATCH = "features.count_mismatch"
FEATURES_UNKNOWN_RECORD = "features.unknown_record"
FEATURES_UNSUPPORTED_RECORD = "features.unsupported_record"
FEATURES_BAD_ATTRIBUTE_REF = "features.bad_attribute_ref"
FEATURES_UNDECLARED_ATTRIBUTE = "features.undeclared_attribute"
FEATURES_MULTIPLE_SURFACES = "features.multiple_surfaces"

# symbols (research 03 R16: strict grammar, never prefix matching)
SYMBOL_STANDARD_GRAMMAR_MISMATCH = "symbol.standard_grammar_mismatch"
SYMBOL_UNKNOWN = "symbol.unknown"
SYMBOL_SHADOWS_STANDARD = "symbol.shadows_standard"
SYMBOL_UNSUPPORTED_SHAPE = "symbol.unsupported_shape"

# step header (AGENTS constraint 1: no nested/arrayed steps)
STEP_NESTED_REPEAT = "stephdr.nested_repeat"
STEP_MULTIPLE = "stephdr.multiple_steps"
STEP_DIR_MISSING = "stephdr.missing_dir"

# layer profile (AGENTS constraint 3: layer profile, then step profile,
# hard error when a layer profile exists but is unusable)
LAYER_PROFILE_MISSING_BOTH = "layer_profile.missing"
LAYER_PROFILE_INVALID = "layer_profile.invalid"

# tools
TOOLS_NO_UNITS = "tools.no_units"
TOOLS_DUPLICATE_NUM = "tools.duplicate_num"

# stackup / Z intervals (AGENTS constraint 3: a dielectric is perforated by the
# drills whose Z interval overlaps it)
STACKUP_DIELECTRIC_SPAN_UNKNOWN = "stackup.dielectric_span_unknown"
STACKUP_DRILL_SPAN_UNKNOWN = "stackup.drill_span_unknown"
STACKUP_THICKNESS_UNKNOWN = "stackup.thickness_unknown"

# geometry expansion (research 05 §12: sweeps, polarity, missing symbols)
GEOMETRY_SYMBOL_UNDEFINED = "geometry.symbol_undefined"
GEOMETRY_SYMBOL_UNSUPPORTED = "geometry.symbol_unsupported"
GEOMETRY_SWEEP_ASYMMETRIC = "geometry.sweep_asymmetric"
GEOMETRY_ARC_DEGENERATE = "geometry.arc_degenerate"
GEOMETRY_EMPTY_RESULT = "geometry.empty_result"
GEOMETRY_RESIZE_UNVERIFIED = "geometry.resize_unverified"
GEOMETRY_SYMBOL_RECURSION = "geometry.symbol_recursion"
GEOMETRY_ZERO_WIDTH_STROKE = "geometry.zero_width_stroke"

# export
EXPORT_NO_GEOMETRY = "export.no_geometry"
EXPORT_LAYER_SELECTION_EMPTY = "export.layer_selection_empty"
EXPORT_LAYER_FAILED = "export.layer_failed"
EXPORT_LAYER_SKIPPED = "export.layer_skipped"
EXPORT_UNKNOWN_LAYER = "export.unknown_layer"
#: Vertices collapsed onto one another at the output grid, so a ring lost all
#: its area; the ring is dropped from the model rather than shipped invalid.
EXPORT_DEGENERATE_RING = "export.degenerate_ring"

# --------------------------------------------------------------------------
# material equivalence (data-flow steps 4-5)
# --------------------------------------------------------------------------

# Board outline rectangularisation (AGENTS 外轮廓矩形化与网格 1-2).
RECTIFY_EMPTY_PROFILE = "rectify.empty_profile"
RECTIFY_NO_RECTANGLES = "rectify.no_rectangles"
RECTIFY_AREA_ERROR = "rectify.area_error"
RECTIFY_ISLAND_LOST = "rectify.island_lost"
RECTIFY_HOLE_FILLED = "rectify.hole_filled"

# Board grid allocation (AGENTS 外轮廓矩形化与网格 3-5).
GRID_FORCED_LINES_OVERFLOW = "grid.forced_lines_overflow"
GRID_NO_BOARD_CELLS = "grid.no_board_cells"
GRID_CELL_WITHOUT_PIXEL = "grid.cell_without_pixel"

# Per-layer evaluation.
EQUIV_LAYER_FAILED = "equiv.layer_failed"
EQUIV_LAYER_SKIPPED = "equiv.layer_skipped"
EQUIV_MISSING_DOMAIN = "equiv.missing_domain"
EQUIV_DIELECTRIC_FEATURES = "equiv.dielectric_features_ignored"
EQUIV_DRILL_SPAN_UNKNOWN = "equiv.drill_span_unknown"
#: A drill had no copper-plane span in the document, so it was matched to a
#: layer by absolute Z alone (safe only when the layer thickness is known).
EQUIV_DRILL_Z_FALLBACK = "equiv.drill_match_z_fallback"
#: Topological copper span and absolute Z select different layers for one drill.
#: Topology is authoritative; the disagreement signals an unreliable best-effort Z.
EQUIV_DRILL_Z_MISMATCH = "equiv.drill_z_mismatch"
EQUIV_LAYER_Z_UNKNOWN = "equiv.layer_z_unknown"
EQUIV_PARTIAL_DOMAIN = "equiv.partial_domain"
EQUIV_LAYER_EMPTY = "equiv.layer_empty"

# Materials (AGENTS 等效器输入与材料规则 4-5).
MATERIALS_DEFAULT_USED = "materials.default_used"
MATERIALS_INVALID = "materials.invalid"
