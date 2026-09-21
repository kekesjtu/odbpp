"""Job assembly: turn an archive into a resolved product model.

This is the orchestrator.  It applies the three AGENTS constraints that are
cross-file concerns rather than per-file syntax:

* **Constraint 1** -- only the single-board step is parsed; nested step arrays
  are reported and ignored (``metadata.parse_step_header`` does the reporting).
* **Constraint 3** -- a layer's base polygon comes from its own ``profile`` when
  that file exists, from the step profile otherwise, and is a hard error when a
  layer profile exists but cannot be used.  See :meth:`Step.base_geometry`.
* **Constraint 4** -- everything is normalised to millimetres on the way in, so
  no downstream code sees a raw unit.

It also applies the two robustness rules discovered by measuring the cases
(``docs/research/03-cases-inventory-and-observations.md``):

* **R1** -- entity lookup is case insensitive, because the matrix writes
  ``F.CU`` where the directory is ``f.cu``.
* **R2** -- the matrix is the authoritative layer list; directories that are not
  registered there are reported and skipped, never parsed into the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .archive import OdbArchive
from .diagnostics import (
    LAYER_DIR_MISSING,
    LAYER_DIR_ORPHAN,
    LAYER_PROFILE_INVALID,
    STEP_DIR_MISSING,
    STEP_MULTIPLE,
    Diagnostics,
)
from .errors import ArchiveError, ParseError
from .features import parse_feature_file, parse_profile
from .metadata import (
    parse_attrlist,
    parse_attribute_definitions,
    parse_matrix,
    parse_product_info,
    parse_step_header,
    parse_tools,
)
from .model import Job, Layer, Step
from .symbols import SymbolLibrary

__all__ = ["LoadOptions", "load_job", "Job"]


@dataclass
class LoadOptions:
    """What to parse, and how strict to be."""

    #: Load only this step (case insensitive).  ``None`` loads every step the
    #: matrix declares, which is what a summariser wants.
    step: Optional[str] = None

    #: Parse ``features`` files.  Turning this off makes enumeration-only runs
    #: (layer inventory, stackup inspection) cheap on large jobs.
    load_features: bool = True

    #: Parse ``tools`` files for drill/rout layers.
    load_tools: bool = True

    #: Validate user-defined symbol references and detect reference cycles.
    check_symbols: bool = True

    #: Attach a :class:`~odbpp.diagnostics.Diagnostics` to the returned job so
    #: callers can inspect warnings without keeping the loader around.
    keep_diagnostics: bool = True


def load_job(
    path: str,
    *,
    options: Optional[LoadOptions] = None,
    diagnostics: Optional[Diagnostics] = None,
) -> Job:
    """Load the ODB++ job at *path* into a :class:`~odbpp.model.Job`.

    *path* may be a directory, ``.tgz``/``.tar.gz``/``.tar`` or ``.zip``.
    """
    opts = options or LoadOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()

    archive = OdbArchive.open(path, diagnostics=diags)
    try:
        return _load(archive, path, opts, diags)
    finally:
        archive.close()


def _load(archive: OdbArchive, source: str, opts: LoadOptions, diags: Diagnostics) -> Job:
    root = archive.require_job_root()

    job = Job(root=root, source_path=source, diagnostics=diags if opts.keep_diagnostics else None)

    job.info = parse_product_info(archive, root, diags)
    job.matrix = parse_matrix(archive, root, diags)
    job.attributes = parse_attribute_definitions(archive, root, diags)

    symbols = SymbolLibrary(
        archive, root,
        diagnostics=diags,
        default_scope=job.info.units,
        definitions=job.attributes,
    )

    step_refs = job.matrix.steps
    if not step_refs:
        # Some exporters omit the STEP array entirely for single-step jobs; the
        # directory listing is then the only source.
        diags.warn(
            STEP_DIR_MISSING,
            "matrix declares no STEP; falling back to the step directories on disk",
            f"{root}/matrix/matrix",
        )
        step_refs = _steps_from_disk(archive, root, diags)

    if opts.step is not None:
        wanted = opts.step.casefold()
        selected = [s for s in step_refs if s.name.casefold() == wanted]
        if not selected:
            available = ", ".join(repr(s.name) for s in step_refs)
            raise ParseError(f"step {opts.step!r} not found; available: {available}",
                             path=f"{root}/matrix/matrix")
        step_refs = selected
    elif len(step_refs) > 1:
        diags.warn(
            STEP_MULTIPLE,
            f"job declares {len(step_refs)} steps ({', '.join(repr(s.name) for s in step_refs)}); "
            f"AGENTS constraint 1 targets a single board step -- select one with --step",
            f"{root}/matrix/matrix",
        )

    referenced_dirs: set[str] = set()

    for step_ref in step_refs:
        step = _load_step(
            archive, root, step_ref.name, job, symbols, opts, diags,
            referenced_dirs,
        )
        if step is not None:
            job.steps[step.name] = step

    job.orphan_step_dirs = _orphan_step_dirs(archive, root, job)

    if opts.check_symbols:
        _validate_symbols(symbols, job, diags)

    if opts.load_features:
        # Materialise the user-defined symbols the loaded layers reference, so
        # the returned job can be rendered after this archive is closed.  The
        # library is attached even when nothing needed preloading, because
        # standard symbols are resolved through it too.
        symbols.preload(_referenced_symbols(symbols, job))
        job.symbols = symbols

    return job


def _referenced_symbols(symbols: SymbolLibrary, job: Job) -> list[str]:
    """Distinct user-defined symbol names referenced by any loaded layer."""
    out: list[str] = []
    for step in job.steps.values():
        for layer in step.layers:
            if layer.features is None:
                continue
            for name in symbols.custom_references(layer.features, job.units.system):
                if name not in out:
                    out.append(name)
    return out


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def _steps_from_disk(archive: OdbArchive, root: str, diags: Diagnostics) -> list:
    from .model import StepRef

    steps_rel = f"{root}/steps" if root else "steps"
    if not archive.is_dir(steps_rel):
        raise ArchiveError(f"job has neither a STEP array nor a steps/ directory: {root!r}")

    out = []
    for index, name in enumerate(archive.list_dir(steps_rel), start=1):
        if archive.exists(f"{steps_rel}/{name}/stephdr") or \
           archive.exists(f"{steps_rel}/{name}/profile"):
            out.append(StepRef(name=name, col=index))
    return out


def _orphan_step_dirs(archive: OdbArchive, root: str, job: Job) -> list[str]:
    steps_rel = f"{root}/steps" if root else "steps"
    if not archive.is_dir(steps_rel):
        return []
    known = {name.casefold() for name in job.steps}
    return [d for d in archive.list_dir(steps_rel) if d.casefold() not in known]


def _load_step(
    archive: OdbArchive,
    root: str,
    step_name: str,
    job: Job,
    symbols: SymbolLibrary,
    opts: LoadOptions,
    diags: Diagnostics,
    referenced_dirs: set[str],
) -> Optional[Step]:
    steps_rel = f"{root}/steps" if root else "steps"

    dir_name = _resolve_child(archive, steps_rel, step_name)
    if dir_name is None:
        diags.warn(
            STEP_DIR_MISSING,
            f"matrix step {step_name!r} has no matching directory under steps/; skipped",
            steps_rel,
        )
        return None

    step_rel = f"{steps_rel}/{dir_name}"
    step = Step(name=step_name, dir_name=dir_name)

    step.header = parse_step_header(
        archive, step_rel, default_scope=job.info.units, diagnostics=diags
    )

    profile_rel = f"{step_rel}/profile"
    if archive.exists(profile_rel):
        step.profile = parse_profile(
            archive, profile_rel,
            default_scope=job.info.units, diagnostics=diags,
            definitions=job.attributes,
        )

    attr_rel = f"{step_rel}/attrlist"
    if archive.exists(attr_rel):
        step.attributes, _ = parse_attrlist(
            archive, attr_rel, default_scope=job.info.units, diagnostics=diags
        )

    layers_rel = f"{step_rel}/layers"
    layer_dirs = archive.list_dir(layers_rel) if archive.is_dir(layers_rel) else []
    dir_by_fold = {d.casefold(): d for d in layer_dirs}

    # Every matrix layer is loaded, including CONTEXT=MISC ones such as EDGE.CUTS:
    # the context says "not part of the board stack", not "not part of the
    # product model", and dropping rows here is how a base outline goes missing.
    # Consumers filter by ``LayerRef.context`` instead.
    for ref in job.matrix.layers:
        folded = ref.name.casefold()
        dir_name = dir_by_fold.get(folded)
        if dir_name is None:
            diags.warn(
                LAYER_DIR_MISSING,
                f"matrix layer {ref.name!r} has no directory under {layers_rel}; "
                f"the product model may be incomplete (spec p.65)",
                layers_rel,
            )
            step.layers.append(Layer(ref=ref, dir_name=""))
            continue

        referenced_dirs.add(f"{step_rel.casefold()}/{folded}")
        step.layers.append(
            _load_layer(archive, f"{layers_rel}/{dir_name}", ref, dir_name,
                        job, symbols, opts, diags)
        )

    # R2: directories that the matrix does not register are not part of the
    # product model.  Report them so a lost layer is visible, but never parse.
    for dir_name in layer_dirs:
        key = f"{step_rel.casefold()}/{dir_name.casefold()}"
        if key not in referenced_dirs:
            diags.warn(
                LAYER_DIR_ORPHAN,
                f"layer directory {dir_name!r} is not registered in the matrix; "
                f"ignored (matrix is authoritative, AGENTS/research 03 R2)",
                f"{layers_rel}/{dir_name}",
            )

    return step


def _resolve_child(archive: OdbArchive, parent: str, name: str) -> Optional[str]:
    """Case-insensitive lookup of a child directory."""
    if not archive.is_dir(parent):
        return None
    folded = name.casefold()
    for child in archive.list_dir(parent):
        if child.casefold() == folded:
            return child
    return None


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------

def _load_layer(
    archive: OdbArchive,
    layer_rel: str,
    ref,
    dir_name: str,
    job: Job,
    symbols: SymbolLibrary,
    opts: LoadOptions,
    diags: Diagnostics,
) -> Layer:
    layer = Layer(ref=ref, dir_name=dir_name)

    attr_rel = f"{layer_rel}/attrlist"
    if archive.exists(attr_rel):
        layer.attributes, _ = parse_attrlist(
            archive, attr_rel, default_scope=job.info.units, diagnostics=diags
        )

    if opts.load_features:
        features_rel = f"{layer_rel}/features"
        if archive.exists(features_rel):
            layer.features = parse_feature_file(
                archive, features_rel,
                default_scope=job.info.units,
                diagnostics=diags,
                definitions=job.attributes,
                require_units=True,
            )

    if opts.load_tools and ref.type is not None and ref.type.is_drill:
        tools_rel = f"{layer_rel}/tools"
        if archive.exists(tools_rel):
            layer.tools = parse_tools(
                archive, tools_rel,
                default_scope=job.info.units, diagnostics=diags,
            )

    # AGENTS constraint 3, layer half: a layer profile, when present, is the
    # base polygon.  A present-but-unparseable profile is a recorded error, not a
    # silent fallback.
    profile_rel = f"{layer_rel}/profile"
    if archive.exists(profile_rel):
        try:
            layer.profile = parse_profile(
                archive, profile_rel,
                default_scope=job.info.units, diagnostics=diags,
                definitions=job.attributes,
            )
            if not layer.profile:
                layer.profile_error = "profile parsed but contains no polygons"
                layer.profile = None
        except ParseError as exc:
            layer.profile_error = str(exc)
            layer.profile = None

        if layer.profile_error is not None:
            diags.error(
                LAYER_PROFILE_INVALID,
                f"layer {ref.name!r} profile exists but is unusable: {layer.profile_error}; "
                f"refusing to fall back to the step profile (AGENTS constraint 3)",
                profile_rel,
            )

    return layer


# ---------------------------------------------------------------------------
# symbol validation
# ---------------------------------------------------------------------------

def _validate_symbols(symbols: SymbolLibrary, job: Job, diags: Diagnostics) -> None:
    """Verify referenced user-defined symbols resolve, and detect cycles.

    Only symbols actually referenced by a loaded layer are validated, because the
    library may legitimately contain hundreds of unused definitions (123 shipped
    vs 9 used in ``cases/designodb-rigidflex``).
    """
    from .errors import SymbolCycleError

    referenced: list[str] = []
    for step in job.steps.values():
        for layer in step.layers:
            if layer.features is None:
                continue
            for name in symbols.custom_references(layer.features):
                if name not in referenced:
                    referenced.append(name)

    for name in referenced:
        try:
            symbols.expand_order(name)
        except SymbolCycleError as exc:
            diags.error(
                "symbol.cycle",
                f"user-defined symbol reference cycle: {' -> '.join(exc.chain)}",
                f"{job.root}/symbols",
            )
