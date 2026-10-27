"""Parsers for the Structured Text metadata files.

Covers ``misc/info``, ``matrix/matrix``, ``steps/<step>/stephdr``, the various
``attrlist`` files, ``misc/sysattr*`` / ``misc/userattr`` and the drill
``tools`` file.

Spec: p.74 (misc/attrlist), p.75 (misc/info), p.62 (matrix), p.84-90 (attribute
definitions), p.139 (stephdr), p.184 (tools).

All of these are ``KEY=VALUE`` / ``BLOCK { }`` documents, so
:class:`~pcbkit.textio.StructuredText` does the lexing and this module only
applies ODB++ semantics.
"""

from __future__ import annotations

from typing import Optional

from .archive import OdbArchive
from .diagnostics import (
    ATTR_DUPLICATE_DEFINITION,
    INFO_MISSING_UNITS,
    MATRIX_DANGLING_CU_REF,
    MATRIX_DUPLICATE_LAYER,
    MATRIX_FORM_CONFLICT,
    MATRIX_FORM_MISSING_DIELECTRIC,
    MATRIX_UNKNOWN_ADD_TYPE,
    MATRIX_UNKNOWN_FORM,
    MATRIX_UNKNOWN_LAYER_TYPE,
    STEP_NESTED_REPEAT,
    TOOLS_DUPLICATE_NUM,
    TOOLS_NO_UNITS,
    Diagnostics,
)
from .errors import ParseError
from .model import (
    FLEX_SUBTYPES,
    AttributeDef,
    AttributeDefinitions,
    DielectricType,
    DrillTool,
    DrillTools,
    DrillToolType,
    FormSource,
    LayerContext,
    LayerForm,
    LayerRef,
    LayerType,
    Matrix,
    Polarity,
    ProductInfo,
    StepHeader,
    StepRef,
    StepRepeat,
)
from .textio import StructuredBlock, StructuredText
from .units import UnitScope, UnitSystem

__all__ = [
    "parse_product_info",
    "parse_matrix",
    "parse_step_header",
    "parse_attrlist",
    "parse_attribute_definitions",
    "parse_tools",
]


def _join(root: str, *parts: str) -> str:
    bits = [p.strip("/") for p in (root, *parts) if p]
    return "/".join(bits)


# ---------------------------------------------------------------------------
# misc/info
# ---------------------------------------------------------------------------

def parse_product_info(
    archive: OdbArchive,
    root: str,
    diagnostics: Diagnostics,
) -> ProductInfo:
    """Parse ``misc/info`` (spec p.75).

    The unit handling here is the *product model default*: every file that
    declares no ``UNITS`` inherits it, and per spec p.23 the fallback when even
    this file is silent is imperial.
    """
    rel = _join(root, "misc/info")
    doc = StructuredText.parse(archive.read_text(rel), path=rel)

    info = ProductInfo(raw=dict(doc.fields))

    units_raw = doc.get("UNITS")
    if units_raw is None or units_raw == "":
        info.units = UnitScope.default(rel)
        info.units_declared = False
        diagnostics.warn(
            INFO_MISSING_UNITS,
            "misc/info has no UNITS directive; falling back to imperial per spec p.23",
            rel,
        )
    else:
        info.units = UnitScope(UnitSystem.parse(units_raw, path=rel), rel)
        info.units_declared = True

    info.job_name = doc.str_field("JOB_NAME")
    info.version_major = doc.int_field("ODB_VERSION_MAJOR", 0) or 0
    info.version_minor = doc.int_field("ODB_VERSION_MINOR", 0) or 0
    info.source = doc.str_field("ODB_SOURCE")
    info.creation_date = doc.str_field("CREATION_DATE")
    info.save_date = doc.str_field("SAVE_DATE")
    info.save_app = doc.str_field("SAVE_APP")
    info.save_user = doc.str_field("SAVE_USER")
    info.max_uid = doc.int_field("MAX_UID")

    return info


# ---------------------------------------------------------------------------
# matrix/matrix
# ---------------------------------------------------------------------------

def parse_matrix(
    archive: OdbArchive,
    root: str,
    diagnostics: Diagnostics,
) -> Matrix:
    """Parse ``matrix/matrix``.

    The matrix is the authoritative layer list: a layer directory that is not
    registered here is not part of the product model, and a matrix layer without
    a directory makes the model unreadable (spec p.65).  Cross-checking the two
    is done by :mod:`pcbkit.job`, which has the directory listing.
    """
    rel = _join(root, "matrix/matrix")
    doc = StructuredText.parse(archive.read_text(rel), path=rel)

    matrix = Matrix()

    for block in doc.blocks_of("STEP"):
        matrix.steps.append(StepRef(
            name=block.str_field("NAME"),
            col=block.int_field("COL", 0) or 0,
            uid=block.int_field("ID"),
        ))

    seen_rows: dict[int, str] = {}
    seen_names: set[str] = set()

    for ordinal, block in enumerate(doc.blocks_of("LAYER"), start=1):
        layer = _parse_layer_block(block, rel, diagnostics, ordinal=ordinal)

        # ``ROW`` is the spec field (p.63) but some exporters omit it; the file
        # order is then the only stacking information available.
        if layer.row in seen_rows:
            diagnostics.warn(
                MATRIX_DUPLICATE_LAYER,
                f"duplicate matrix ROW={layer.row} ({layer.name!r} collides with "
                f"{seen_rows[layer.row]!r})",
                rel,
            )
        seen_rows.setdefault(layer.row, layer.name)

        folded = layer.name.casefold()
        if folded in seen_names:
            diagnostics.warn(
                MATRIX_DUPLICATE_LAYER,
                f"duplicate matrix layer NAME={layer.name!r}",
                rel,
            )
        seen_names.add(folded)

        matrix.layers.append(layer)

    # ROW order defines physical stacking order, so sort once here and let every
    # consumer rely on it.
    matrix.layers.sort(key=lambda l: l.row)
    matrix.steps.sort(key=lambda s: s.col)

    _check_cu_references(matrix, rel, diagnostics)

    return matrix


def _parse_layer_block(
    block: StructuredBlock,
    path: str,
    diagnostics: Diagnostics,
    *,
    ordinal: int,
) -> LayerRef:
    raw_type = block.str_field("TYPE").strip().upper()
    layer_type: Optional[LayerType] = None
    if raw_type:
        try:
            layer_type = LayerType(raw_type)
        except ValueError:
            diagnostics.warn(
                MATRIX_UNKNOWN_LAYER_TYPE,
                f"layer {block.str_field('NAME')!r} has unknown TYPE={raw_type!r}",
                path,
            )

    add_type = block.str_field("ADD_TYPE").strip().upper()
    if add_type and add_type not in _KNOWN_ADD_TYPES:
        diagnostics.warn(
            MATRIX_UNKNOWN_ADD_TYPE,
            f"layer {block.str_field('NAME')!r} has unrecognised ADD_TYPE={add_type!r}",
            path,
        )

    polarity_raw = block.str_field("POLARITY", "POSITIVE").strip().upper()
    if polarity_raw not in ("POSITIVE", "NEGATIVE"):
        raise ParseError(
            f"layer {block.str_field('NAME')!r} has invalid POLARITY={polarity_raw!r}",
            path=path,
            line_no=block.line_no,
        )

    # Spec p.66 gives FORM a default, so a missing field is *not* unknown:
    #   FORM ... (optional) (default = RIGID)
    #   Layers of the subtype SIGNAL_FLEX, PG_FLEX and MIXED_FLEX have a
    #   default FORM = FLEX.
    # There are exactly three ways to reach a value, and which one applied is
    # worth keeping: on a DIELECTRIC layer, "RIGID because the spec says so" and
    # "RIGID because the exporter was silent" are not equally strong claims.
    form_raw = block.str_field("FORM", "").strip().upper()
    if form_raw in ("RIGID", "FLEX"):
        form = LayerForm(form_raw)
        form_source = FormSource.DECLARED
    else:
        if form_raw:
            diagnostics.warn(
                MATRIX_UNKNOWN_FORM,
                f"layer {block.str_field('NAME')!r} has unrecognised "
                f"FORM={form_raw!r}; falling back to the subtype/default rule",
                path,
            )
        flex_subtype = add_type in FLEX_SUBTYPES
        form = LayerForm.FLEX if flex_subtype else LayerForm.RIGID
        form_source = (FormSource.FLEX_SUBTYPE if flex_subtype
                       else FormSource.DEFAULT)

    # A declared FORM wins over the subtype default (it *is* a default, not a
    # constraint), but the pair disagreeing means one of the two is wrong -- and
    # an exporter emitting FORM=RIGID on a PG_FLEX layer would otherwise be
    # invisible.
    if (form_source is FormSource.DECLARED and add_type in FLEX_SUBTYPES
            and form is not LayerForm.FLEX):
        diagnostics.warn(
            MATRIX_FORM_CONFLICT,
            f"layer {block.str_field('NAME')!r} declares FORM={form_raw} but has "
            f"ADD_TYPE={add_type}, whose default FORM is FLEX; taking the "
            f"declared value",
            path,
        )

    # Spec p.66: "Layers of type DIELECTRIC are to be defined as RIGID or FLEX."
    # That is the one layer class where the spec makes the field expected rather
    # than optional, and the one where a wrong answer moves the flex boundary --
    # so silence is reported rather than quietly defaulted.
    if layer_type is LayerType.DIELECTRIC and form_source is FormSource.DEFAULT:
        diagnostics.warn(
            MATRIX_FORM_MISSING_DIELECTRIC,
            f"DIELECTRIC layer {block.str_field('NAME')!r} does not declare FORM, "
            f"which spec p.66 says dielectric layers are to define; assuming the "
            f"RIGID default",
            path,
        )

    diel_raw = block.str_field("DIELECTRIC_TYPE", "NONE").strip().upper() or "NONE"
    try:
        diel_type = DielectricType(diel_raw)
    except ValueError:
        diel_type = DielectricType.NONE

    return LayerRef(
        name=block.str_field("NAME"),
        row=block.int_field("ROW", ordinal) or ordinal,
        context=(LayerContext.MISC if block.str_field("CONTEXT", "BOARD").strip().upper() == "MISC"
                 else LayerContext.BOARD),
        type=layer_type,
        raw_type=raw_type,
        add_type=add_type,
        polarity=(Polarity.NEGATIVE if polarity_raw == "NEGATIVE" else Polarity.POSITIVE),
        uid=block.int_field("ID"),
        form=form,
        form_source=form_source,
        dielectric_type=diel_type,
        dielectric_name=block.str_field("DIELECTRIC_NAME"),
        cu_top=block.int_field("CU_TOP"),
        cu_bottom=block.int_field("CU_BOTTOM"),
        ref=block.int_field("REF"),
        old_name=block.str_field("OLD_NAME"),
        start_name=block.str_field("START_NAME"),
        end_name=block.str_field("END_NAME"),
        color=block.str_field("COLOR"),
    )


#: Layer subtypes listed in spec p.68-71 (table ``Layer Subtypes``).
_KNOWN_ADD_TYPES: frozenset[str] = frozenset({
    "PUNCH",
    "BACKDRILL", "DUAL_DIAMETER",
    "EMBEDDED_R", "EMBEDDED_C",
    "CARBON_MASK", "SILVER_MASK",
    "AREA", "DRAWING",
    "BEND_AREA", "FLEX_AREA", "RIGID_AREA",
    "IMMERSION_MASK", "OSP_MASK", "PLATING_MASK",
    "STIFFENER", "FR4_STIFFENER", "METAL_STIFFENER", "POLYIMIDE_STIFFENER",
    "FILM_SHIELDING", "METAL_SHIELDING", "PSA",
    "MIXED_FLEX", "PG_FLEX", "SIGNAL_FLEX",
    "COVERCOAT", "COVERLAY", "LIQUID_PHOTO_IMAGEABLE",
    "WIRE_BONDING",
})


def _check_cu_references(matrix: Matrix, path: str, diagnostics: Diagnostics) -> None:
    """Verify ``CU_TOP``/``CU_BOTTOM``/``REF`` point at layers that exist.

    These fields hold layer **UIDs**, not names (spec p.66-67), so a typo
    silently detaches a dielectric from the copper it separates.
    """
    uids = {l.uid for l in matrix.layers if l.uid is not None}
    for layer in matrix.layers:
        for field_name in ("cu_top", "cu_bottom", "ref"):
            value = getattr(layer, field_name)
            if value is None:
                continue
            if value not in uids:
                diagnostics.warn(
                    MATRIX_DANGLING_CU_REF,
                    f"layer {layer.name!r} field {field_name.upper()}={value} does not "
                    f"reference any layer ID",
                    path,
                )


# ---------------------------------------------------------------------------
# stephdr
# ---------------------------------------------------------------------------

def parse_step_header(
    archive: OdbArchive,
    step_rel: str,
    *,
    default_scope: UnitScope,
    diagnostics: Diagnostics,
) -> StepHeader:
    """Parse ``steps/<step>/stephdr`` (spec p.139).

    Nested step arrays (``DX``/``DY``/``NX``/``NY``) are recorded and reported,
    never applied: requirements.md parser constraint 1 restricts this parser to the single-board
    step and excludes panel matrices.
    """
    rel = f"{step_rel}/stephdr"
    doc = StructuredText.parse(archive.read_text(rel), path=rel)

    header = StepHeader(raw=dict(doc.fields))

    units_raw = doc.get("UNITS")
    header.units = (UnitScope(UnitSystem.parse(units_raw, path=rel), rel)
                    if units_raw else default_scope)

    header.x_datum = header.units.coord_mm(doc.float_field("X_DATUM", 0.0) or 0.0)
    header.y_datum = header.units.coord_mm(doc.float_field("Y_DATUM", 0.0) or 0.0)
    header.x_origin = header.units.coord_mm(doc.float_field("X_ORIGIN", 0.0) or 0.0)
    header.y_origin = header.units.coord_mm(doc.float_field("Y_ORIGIN", 0.0) or 0.0)
    header.affecting_bom = doc.str_field("AFFECTING_BOM")
    header.uid = doc.int_field("ID")

    repeats = doc.blocks_of("STEP-REPEAT") + doc.blocks_of("STEP_REPEAT")
    for block in repeats:
        repeat = _parse_step_repeat(block, header.units)
        header.repeats.append(repeat)

    # A repeat array can also be expressed with top-level DX/DY/NX/NY fields.
    if not repeats and any(doc.get(k) not in (None, "") for k in ("DX", "DY", "NX", "NY")):
        header.repeats.append(StepRepeat(
            dx=header.units.coord_mm(doc.float_field("DX", 0.0) or 0.0),
            dy=header.units.coord_mm(doc.float_field("DY", 0.0) or 0.0),
            nx=doc.int_field("NX", 1) or 1,
            ny=doc.int_field("NY", 1) or 1,
        ))

    if header.repeats:
        diagnostics.warn(
            STEP_NESTED_REPEAT,
            f"step {step_rel.rsplit('/', 1)[-1]!r} declares {len(header.repeats)} nested "
            f"step repeat(s); requirements.md parser constraint 1 excludes panel arrays, so they are ignored",
            rel,
        )

    return header


def _parse_step_repeat(block: StructuredBlock, scope: UnitScope) -> StepRepeat:
    return StepRepeat(
        name=block.str_field("NAME"),
        x=scope.coord_mm(block.float_field("X", 0.0) or 0.0),
        y=scope.coord_mm(block.float_field("Y", 0.0) or 0.0),
        dx=scope.coord_mm(block.float_field("DX", 0.0) or 0.0),
        dy=scope.coord_mm(block.float_field("DY", 0.0) or 0.0),
        nx=block.int_field("NX", 1) or 1,
        ny=block.int_field("NY", 1) or 1,
        angle=block.float_field("ANGLE", 0.0) or 0.0,
        mirrored=block.str_field("MIRROR", "N").strip().upper().startswith("Y"),
    )


# ---------------------------------------------------------------------------
# attrlist (product / step / layer / symbol / wheel level)
# ---------------------------------------------------------------------------

def parse_attrlist(
    archive: OdbArchive,
    relpath: str,
    *,
    default_scope: UnitScope,
    diagnostics: Diagnostics,
) -> tuple[dict[str, str], UnitScope]:
    """Parse an ``attrlist`` file: ``<attribute> = <value>`` lines (spec p.74).

    Returns ``(attributes, scope)``.
    """
    doc = StructuredText.parse(archive.read_text(relpath), path=relpath)

    units_raw = doc.get("UNITS")
    scope = (UnitScope(UnitSystem.parse(units_raw, path=relpath), relpath)
             if units_raw else default_scope)

    return dict(doc.fields), scope


# ---------------------------------------------------------------------------
# attribute definitions
# ---------------------------------------------------------------------------

_SYSATTR_FILES = (
    "sysattr",
    "sysattr.dfm",
    "sysattr.fab",
    "sysattr.assy",
    "sysattr.test",
    "sysattr.gen",
    "userattr",
)


def parse_attribute_definitions(
    archive: OdbArchive,
    root: str,
    diagnostics: Diagnostics,
) -> AttributeDefinitions:
    """Parse every attribute definition file under ``misc/`` (spec p.84-90).

    These files are optional and frequently absent, in which case attribute
    values keep their raw token form (see
    :func:`pcbkit.features.resolve_attributes`).
    """
    result = AttributeDefinitions()

    for filename in _SYSATTR_FILES:
        rel = _join(root, "misc", filename)
        if not archive.exists(rel):
            continue
        doc = StructuredText.parse(archive.read_text(rel), path=rel)
        for block in doc.blocks:
            # The block keyword is the attribute type: BOOLEAN / TEXT / OPTION /
            # INTEGER / FLOAT.
            definition = _parse_attribute_block(block, rel)
            if definition is None:
                continue
            if definition.name in result.defs:
                diagnostics.warn(
                    ATTR_DUPLICATE_DEFINITION,
                    f"attribute {definition.name!r} is defined more than once",
                    rel,
                )
                continue
            result.defs[definition.name] = definition

    return result


def _parse_attribute_block(block: StructuredBlock, path: str) -> Optional[AttributeDef]:
    name = block.str_field("NAME").strip()
    if not name:
        return None

    options_raw = block.str_field("OPTIONS")
    options = [o.strip() for o in options_raw.split(";") if o.strip()] if options_raw else []

    deleted_raw = block.str_field("DELETED")
    if deleted_raw and options:
        flags = [f.strip().upper() for f in deleted_raw.split(";")]
        # "A value of YES indicates that the corresponding OPTIONS value is to be
        # ignored" (spec p.86-87), so keep only the usable options.
        options = [opt for opt, flag in zip(options, flags) if not flag.startswith("Y")]

    return AttributeDef(
        name=name,
        type=block.keyword,
        entity=block.str_field("ENTITY"),
        cls=block.str_field("GROUP"),
        options=options,
        default=block.str_field("DEF"),
        unit=block.str_field("UNIT"),
        source=path,
    )


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def parse_tools(
    archive: OdbArchive,
    relpath: str,
    *,
    default_scope: UnitScope,
    diagnostics: Diagnostics,
) -> DrillTools:
    """Parse a drill/rout ``tools`` file (spec p.184).

    ``DRILL_SIZE``/``FINISH_SIZE`` use the **size** unit semantic (micrometre or
    mil), not the coordinate one, so they go through ``UnitScope.size_mm``.
    Tool ``TYPE`` is the authoritative plating flag -- a layer named
    ``DRILL_PLATED_*`` may still contain ``NON_PLATED`` tools, as
    ``cases/designodb-rigidflex`` does.
    """
    doc = StructuredText.parse(archive.read_text(relpath), path=relpath)

    units_raw = doc.get("UNITS")
    if units_raw:
        scope = UnitScope(UnitSystem.parse(units_raw, path=relpath), relpath)
    else:
        scope = default_scope
        diagnostics.warn(
            TOOLS_NO_UNITS,
            "tools file has no UNITS directive; falling back to the product model default",
            relpath,
        )

    result = DrillTools(units=scope)
    result.thickness_mm = scope.size_mm(doc.float_field("THICKNESS", 0.0) or 0.0)

    for block in doc.blocks_of("TOOLS"):
        num = block.int_field("NUM", 0) or 0
        if num in result.tools:
            diagnostics.warn(
                TOOLS_DUPLICATE_NUM,
                f"duplicate drill tool NUM={num}",
                relpath,
            )
            continue

        raw_type = block.str_field("TYPE", "PLATED").strip().upper()
        try:
            tool_type = DrillToolType(raw_type)
        except ValueError:
            tool_type = DrillToolType.PLATED

        raw_finish = block.float_field("FINISH_SIZE", 0.0) or 0.0
        raw_drill = block.float_field("DRILL_SIZE", 0.0) or 0.0

        result.tools[num] = DrillTool(
            num=num,
            type=tool_type,
            type2=block.str_field("TYPE2", "STANDARD"),
            min_tol_mm=scope.size_mm(block.float_field("MIN_TOL", 0.0) or 0.0),
            max_tol_mm=scope.size_mm(block.float_field("MAX_TOL", 0.0) or 0.0),
            bit=block.str_field("BIT"),
            finish_size_mm=scope.size_mm(raw_finish),
            drill_size_mm=scope.size_mm(raw_drill),
            raw_finish_size=raw_finish,
            raw_drill_size=raw_drill,
            raw=dict(block.fields),
        )

    return result
