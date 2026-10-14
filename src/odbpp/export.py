"""The render-ready model: a job composed into per-layer positive polygons.

This is data-flow step 2.  It sits between the parser (which knows what the files
say) and any renderer (which needs flat polygons in millimetres), and it is the
only place that decides *what a layer's area actually is*.

Design notes
------------
**Millimetres, always.**  Input may be ``INCH`` or ``MM``; the model is metric
(AGENTS constraint 4).  No unit is ever reported as a raw file value.

**Positive only.**  Every ring that comes out is a real boundary: islands wound
clockwise, holes counter-clockwise (spec p.39).  A renderer needs no fill-rule
trickery -- and in particular must not use ``evenodd``, which would punch a hole
where two positive features overlap.

**Curves are gone by this point.**  Booleans cannot carry arcs, so the model is
polygonal.  ``tessellation.sagitta_mm`` is therefore the last point at which
curve fidelity is decided, which is why it is an option on the *preview* and not
buried in the parser.

**Geometry is streamed, not cached.**  Composing ``designodb-rigidflex``
(520k records) yields 29,235 polygons for a single assembly layer.  Holding every
layer's polygons while also holding the SVG text hundreds of megabytes.  Callers
that render layer by layer should use :func:`iter_layer_previews` and drop each
layer after writing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Optional, Sequence
from .clipper import Ring, region_from_geometry, snap_to_grid
from .diagnostics import (
    EXPORT_DEGENERATE_RING,
    EXPORT_LAYER_FAILED,
    EXPORT_LAYER_SKIPPED,
    EXPORT_NO_GEOMETRY,
    EXPORT_UNKNOWN_LAYER,
    Diagnostic,
    Diagnostics,
)
from .errors import LayerProfileError
from .expand import ExpandOptions, Expander, LayerGeometry, expand_layer
from .geometry import TessellationOptions
from .model import Job, Layer, Step
from .stackup import Stackup, describe_span
from .intermediate import BoardDocument, LayerModel


__all__ = [
    "step_to_document",
    "PreviewOptions",
    "LayerPreview",
    "StepPreview",
    "build_step_preview",
    "iter_step_previews",
    "regularise_preview",
    "layer_to_json",
    "step_to_json",
    "layer_summary_lines",
    "write_json",
]

@dataclass(frozen=True)
class PreviewOptions:
    """What to compose and how faithfully."""

    tessellation: TessellationOptions = field(default_factory=TessellationOptions)
    #: Layer names to include (case-insensitive).  ``None`` means every layer.
    layers: Optional[Sequence[str]] = None
    #: Skip layers of these types, e.g. ``("DOCUMENT",)`` for fabrication
    #: drawings whose hundreds of thousands of records dwarf everything else.
    exclude_types: Sequence[str] = ()
    #: Render a zero-width stroke (``r0``) as a hairline of this width.
    #:
    #: Real jobs do contain ``r0`` lines -- ``designodb-rigidflex`` draws its
    #: board outline with 235 of them -- and mathematically they enclose no area,
    #: so by default they vanish.  That is honest but not useful, so the width is
    #: adjustable *and* the fact is reported rather than silently normalised.
    minimum_stroke_mm: float = 0.0
    #: Decimal places for coordinates in the JSON and SVG.
    #:
    #: This is also the model's **resolution**, not merely a printing choice:
    #: composed geometry is snapped to the ``10**-round_digits`` mm grid and
    #: re-regularised before it leaves the pipeline, so the polygons a consumer
    #: reads back are the ones that were validated.  4 is 0.1 um, far finer than
    #: any fabrication tolerance and much smaller on disk.
    round_digits: int = 4
    #: Report user-defined symbols used to sweep lines/arcs (spec p.38).
    warn_unsupported_strokes: bool = True

    def expand_options(self) -> ExpandOptions:
        return ExpandOptions(
            tessellation=self.tessellation,
            warn_unsupported_strokes=self.warn_unsupported_strokes,
            minimum_stroke_mm=self.minimum_stroke_mm,
        )


# ---------------------------------------------------------------------------
# per-layer preview
# ---------------------------------------------------------------------------

@dataclass
class LayerPreview:
    """One composed layer, ready to serialise or draw."""

    layer: Layer
    geometry: LayerGeometry
    #: The step's stack model, when the preview came from the export pipeline.
    #: ``None`` for hand-built previews; every accessor below tolerates that.
    stackup: Optional[Stackup] = None
    #: Decimal places of the coordinate grid *geometry* was regularised onto.
    #: ``None`` for hand-built previews.  Serialisation defaults to this, so the
    #: polygons written are the ones that were validated; asking for a different
    #: precision re-regularises instead of silently rounding points.
    round_digits: Optional[int] = None
    drawing_only: bool = field(default=False, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.layer.name

    @property
    def type_name(self) -> str:
        return self.layer.type.value if self.layer.type is not None else "UNKNOWN"

    @property
    def is_copper(self) -> bool:
        return self.layer.is_copper

    @property
    def is_negative(self) -> bool:
        return self.layer.is_negative

    @property
    def rings(self) -> list[Ring]:
        return self.geometry.region.rings

    @property
    def area_mm2(self) -> float:
        return self.geometry.area_mm2

    @property
    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        return self.geometry.bounds

    @property
    def is_empty(self) -> bool:
        return not self.geometry.region

    def layer_attributes(self) -> dict[str, str]:
        """Layer-level attributes worth surfacing, per AGENTS note 4.

        ``.layer_dielectric`` is a *Layer*-class field and is meaningful only for
        non-copper layers; ``.copper_weight`` changes physical meaning with the
        unit system (spec p.24), so both are reported as authored here.  The
        *interpreted* values are separate fields -- see
        :meth:`normalised_attributes` -- because a single number cannot carry
        both the raw file text and the millimetre value without inviting
        exactly the confusion AGENTS note 4 warns about.
        """
        wanted = (".layer_dielectric", ".copper_weight", ".layer_sequence",
                  ".dielectric_name", ".polarity")
        out: dict[str, str] = {}
        for key, value in self.layer.attributes.items():
            if key.casefold() in wanted:
                out[key] = value
        return out

    def attribute_units(self) -> Optional[dict]:
        """Unit scope the layer's ``attrlist`` was written under.

        Without this a consumer cannot tell ``.layer_dielectric = 0.00255906``
        (65 um in an INCH job) from the same number in an MM job, because the
        document's top-level ``units: mm`` describes the polygons, not the
        attributes they came from.
        """
        scope = getattr(self.layer, "attribute_scope", None)
        if scope is None:
            return None
        return {
            "system": scope.system.value,
            "origin": scope.origin,
        }

    def thickness(self):
        """Interpreted ``.layer_dielectric`` as a :class:`~odbpp.stackup.Thickness`."""
        if self.stackup is None:
            return None
        from .stackup import Thickness  # noqa: F401 - documents the return type
        return self.stackup.thickness(self.layer)

    def copper_weight(self):
        """Interpreted ``.copper_weight``; a density in imperial, a thickness in metric."""
        if self.stackup is None:
            return None
        return self.stackup.weight(self.layer)

    def z_span(self) -> Optional[dict]:
        """Which copper planes this layer sits between, or ``None``."""
        if self.stackup is None:
            return None
        return describe_span(self.stackup, self.stackup.span_of(self.layer))

    def z_extent(self):
        """Absolute millimetre Z extent (:class:`~odbpp.stackup.ZExtent`).

        ``None`` when the preview has no stack model, or when the layer is not
        part of the board (documentation, component outlines).  See
        :meth:`StepPreview.z_axis` for the axis convention.
        """
        if self.stackup is None:
            return None
        return self.stackup.z_extent(self.layer)


@dataclass
class StepPreview:
    """A step composed into per-layer polygons, plus stackup context."""

    step: Step
    layers: list[LayerPreview]
    units_in: str
    diagnostics: Diagnostics
    #: Layers that composed successfully to zero area -- usually intentional, and
    #: most often zero-width (``r0``) artwork.  Kept distinct from
    #: ``failed_layers`` because "this layer is empty" and "this layer could not
    #: be read" call for opposite responses from whoever is looking at the
    #: preview.
    empty_layers: list[str] = field(default_factory=list)
    #: Layers that could not be composed at all, e.g. an unusable layer profile
    #: (AGENTS constraint 3).  Their absence from the drawing is a bug or a data
    #: problem, not a property of the board.
    failed_layers: list[str] = field(default_factory=list)
    #: The step's resolved layer stack (copper order, dielectric/drill Z spans,
    #: thicknesses).  ``None`` only for hand-built previews.
    stackup: Optional[Stackup] = None
    #: Coordinate-grid resolution the layers were regularised onto; see
    #: :attr:`LayerPreview.round_digits`.
    round_digits: Optional[int] = None

    #: Convention of the ``z_mm`` extents in this step's JSON.  Z is a board
    #: axis, not a model coordinate: it runs downward from the top surface and
    #: is independent of the Y-up model plane.
    Z_AXIS = "increases downward from the top of the stack (matrix row 1), mm"

    #: Used only when drawing a filtered dielectric with omitted drill geometry.
    #: Retain the library/options, not the expander's large geometry caches.
    source_symbols: Optional[object] = field(default=None, repr=False, compare=False)
    expansion_options: Optional[ExpandOptions] = field(default=None, repr=False, compare=False)
    drawing_only: bool = field(default=False, repr=False, compare=False)

    def z_axis(self) -> str:
        return self.Z_AXIS

    @property
    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        boxes = [box for l in self.layers if (box := l.bounds) is not None]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    @property
    def total_polygons(self) -> int:
        return sum(len(l.rings) for l in self.layers)

    def layer(self, name: str) -> Optional[LayerPreview]:
        folded = name.casefold()
        return next((l for l in self.layers if l.name.casefold() == folded), None)

    def non_empty(self) -> list[LayerPreview]:
        return [l for l in self.layers if not l.is_empty]


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

def _selected_layers(step: Step, options: PreviewOptions,
                     diagnostics: Optional[Diagnostics] = None) -> list[Layer]:
    """Layers chosen by ``options``, in matrix row order.

    A ``--layer`` name that matches nothing is reported: silently exporting
    nothing looks identical to a layer that is genuinely empty, and the two want
    opposite responses.
    """
    layers = list(step.layers)

    if options.layers:
        wanted = {name.casefold() for name in options.layers}
        found = {l.name.casefold() for l in layers}
        if diagnostics is not None:
            for name in options.layers:
                if name.casefold() not in found:
                    diagnostics.error(
                        EXPORT_UNKNOWN_LAYER,
                        f"no layer named {name!r} in step {step.name!r}; "
                        f"available: {', '.join(sorted(l.name for l in layers))}",
                    )
        layers = [l for l in layers if l.name.casefold() in wanted]

    if options.exclude_types:
        excluded = {t.upper() for t in options.exclude_types}
        layers = [l for l in layers
                  if (l.type.value if l.type is not None else "UNKNOWN") not in excluded]

    return layers


def iter_step_previews(
    job: Job,
    step: Step,
    *,
    options: Optional[PreviewOptions] = None,
    diagnostics: Optional[Diagnostics] = None,
    expander: Optional[Expander] = None,
) -> Iterator[LayerPreview]:
    """Yield one composed :class:`LayerPreview` per layer, in matrix row order.

    Streaming matters: the largest single layer in ``cases/`` yields 29,235
    polygons, and a caller that renders as it goes never holds more than one
    layer's worth of rings.
    """
    opts = options or PreviewOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()
    expander = expander or _make_expander(job, opts, diags)
    # One stack model per step; its spans are exported and used later by the
    # dielectric renderer. Cross-layer drills do not modify parser geometry.
    stackup = expander.stackup(step)

    for layer in _selected_layers(step, opts, diags):
        if layer.features is None and layer.profile is None and step.profile is None:
            continue
        try:
            geometry = expand_layer(layer, step, expander=expander,
                                    options=opts.expand_options())
        except LayerProfileError as exc:
            # AGENTS constraint 3: a layer profile that exists but is unusable is
            # a hard error.  The loader has already reported *why* (as
            # layer_profile.invalid, with the profile's own path); what is
            # reported here is the consequence -- this layer is absent from the
            # preview -- under its own code, so neither message is a restatement
            # of the other.  One bad layer does not cost the whole preview.
            diags.error(EXPORT_LAYER_SKIPPED,
                        f"layer {layer.name!r} is not in the preview: {exc}",
                        layer.dir_name or None)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad layer must not kill the run
            diags.error(EXPORT_LAYER_FAILED,
                        f"layer {layer.name!r} could not be composed: "
                        f"{type(exc).__name__}: {exc}",
                        layer.dir_name or None)
            continue

        # Quantise to the output grid and re-validate *before* the geometry is
        # called a result.  Areas and bounds below are recomputed from these
        # coordinates, so what the JSON reports is what the JSON contains.
        geometry.region, collapsed = snap_to_grid(geometry.region, opts.round_digits)
        if collapsed:
            diags.warn(
                EXPORT_DEGENERATE_RING,
                f"layer {layer.name!r}: {collapsed} ring(s) had no area left at "
                f"the {opts.round_digits}-decimal output grid and were dropped; "
                f"the remaining geometry was re-regularised at that resolution",
                layer.dir_name or None,
            )
        yield LayerPreview(layer=layer, geometry=geometry, stackup=stackup,
                           round_digits=opts.round_digits)


def regularise_preview(
    preview: StepPreview,
    round_digits: int,
    *,
    diagnostics: Optional[Diagnostics] = None,
) -> StepPreview:
    """Return *preview* re-regularised onto the ``10**-round_digits`` mm grid.

    Every artefact derived from the model -- JSON, SVG, HTML -- has to describe
    the geometry it actually contains.  Printing live coordinates at a coarser
    precision is not the same thing: vertices merge, a sliver collapses onto a
    line, and the reported area, ring count and bounds keep describing the shape
    that was thrown away.  Re-quantising the *region* (which also resolves
    crossings and re-derives island/hole nesting) is what makes the written
    polygons, and the numbers that describe them, one geometry again.

    A preview already at the requested resolution is returned unchanged, so the
    common path costs nothing.  Layers that lose their last ring are dropped and
    reported, exactly as during composition; ``empty_layers`` is recomputed from
    the result rather than copied, because it is a statement about the geometry
    that will be written.
    """
    digits = max(0, int(round_digits))
    own = getattr(preview, "round_digits", None)
    if own is not None and int(own) == digits:
        return preview

    diags = diagnostics if diagnostics is not None else preview.diagnostics
    layers: list[LayerPreview] = []
    for layer in preview.layers:
        region, collapsed = snap_to_grid(layer.geometry.region, digits)
        if collapsed:
            diags.warn(
                EXPORT_DEGENERATE_RING,
                f"layer {layer.name!r}: {collapsed} ring(s) had no area left at "
                f"the {digits}-decimal output grid and were dropped; "
                f"the remaining geometry was re-regularised at that resolution",
                layer.layer.dir_name or None,
            )
        geometry = replace(layer.geometry, region=region)
        layers.append(replace(layer, geometry=geometry, round_digits=digits))

    return replace(
        preview,
        layers=layers,
        empty_layers=[l.name for l in layers if l.is_empty],
        round_digits=digits,
    )


def _make_expander(job: Job, options: PreviewOptions,
                   diagnostics: Diagnostics) -> Expander:
    """Build the expander for *job*.

    ``job.symbols`` is populated by the loader with the definitions the layers
    reference, so it is normally present.  A job loaded with
    ``load_features=False`` -- or built by hand in a test -- has none, and then
    only standard symbols can be expanded; user-defined ones are reported as
    unresolved by the expander rather than crashing here.
    """
    return Expander(job.symbols, options=options.expand_options(),
                    diagnostics=diagnostics)


def build_step_preview(
    job: Job,
    step: Optional[Step] = None,
    *,
    options: Optional[PreviewOptions] = None,
    diagnostics: Optional[Diagnostics] = None,
) -> StepPreview:
    """Compose one step completely, holding every layer's polygons in memory."""
    opts = options or PreviewOptions()
    diags = diagnostics if diagnostics is not None else (
        job.diagnostics if isinstance(job.diagnostics, Diagnostics) else Diagnostics()
    )
    step = step or job.single_step

    expander = _make_expander(job, opts, diags)
    stackup = expander.stackup(step)
    layers = list(iter_step_previews(job, step, options=opts,
                                     diagnostics=diags, expander=expander))

    selected = _selected_layers(step, opts, diags)
    produced = {l.name.casefold() for l in layers}
    # Original spellings, not case-folded keys: these names are shown to a user.
    unavailable = [l.name for l in selected if l.name.casefold() not in produced]

    empty = [l.name for l in layers if l.is_empty]
    if not any(not l.is_empty for l in layers):
        diags.error(
            EXPORT_NO_GEOMETRY,
            f"every selected layer of step {step.name!r} composed to an empty "
            f"region ({len(selected)} layer(s) selected)",
        )

    return StepPreview(
        step=step,
        layers=layers,
        units_in=job.units.system.value,
        diagnostics=diags,
        empty_layers=empty,
        # A layer that composed to nothing still produced a LayerPreview; one
        # that raised did not.  That difference is what separates "empty" from
        # "missing".
        failed_layers=[name for name in unavailable if name not in empty],
        stackup=stackup,
        round_digits=opts.round_digits,
        source_symbols=job.symbols,
        expansion_options=opts.expand_options(),
    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _round(value: float, digits: int) -> float:
    out = round(float(value) + 0.0, digits)
    return 0.0 if out == 0.0 else out


def _points_json(ring: Ring, digits: int) -> list[list[float]]:
    return [[_round(x, digits), _round(y, digits)] for x, y in ring.points]


def _preview_digits(preview, round_digits: Optional[int]) -> int:
    """Decimal places to serialise *preview* at.

    An explicit request wins; otherwise the preview's own model resolution is
    used, so the polygons written are the ones that were validated.  A
    hand-built preview without a recorded resolution falls back to 4, which is
    the historical default.
    """
    if round_digits is not None:
        return max(0, int(round_digits))
    own = getattr(preview, "round_digits", None)
    if own is not None:
        return max(0, int(own))
    return 4


def _serialised_rings(preview, digits: int) -> tuple[list[Ring], float, Optional[tuple]]:
    """Rings, area and bounds as they will be written at *digits*.

    When the request disagrees with the resolution the preview was built at, the
    region is re-regularised onto the requested grid rather than rounded point
    by point.  Rounding alone can flatten a ring to zero area or cross two edges
    while ``area_mm2`` still describes the pre-rounding shape; re-quantising and
    recomputing keeps the reported numbers and the written polygons the same
    geometry.
    """
    own = getattr(preview, "round_digits", None)
    if own is None or int(own) == digits:
        return preview.rings, preview.area_mm2, preview.bounds
    region, _collapsed = snap_to_grid(preview.geometry.region, digits)
    return region.rings, region.area, region.bounds


def _layer_metadata(layer: Layer, stackup: Optional[Stackup], digits: int) -> dict:
    """One authoritative layer record; numerical thickness is derived from Z."""
    ref = layer.ref
    extent = None if stackup is None else stackup.z_extent(layer)
    span = None if stackup is None else stackup.span_of(layer)
    thickness = None
    if stackup is not None and layer.type is not None and (
            layer.is_copper or layer.type.is_non_copper_material):
        _value, known, source = stackup._material_thickness(layer)
        thickness = {"known": known, "source": source}
        weight = stackup.weight(layer) if layer.is_copper else None
        stated = stackup.thickness(layer) if weight is None else None
        if weight is not None:
            thickness.update(input_value=weight.raw, kind=weight.kind.value,
                             unit="um" if not weight.is_thickness_derived else "oz/ft2",
                             thickness_source="assumed_density" if weight.is_thickness_derived else "attribute")
            if weight.is_thickness_derived:
                thickness["assumed_density_g_cm3"] = weight.density_g_cm3
        elif stated is not None:
            thickness.update(input_value=stated.raw, kind="thickness",
                             unit="mm" if stated.unit_system == "MM" else "inch",
                             thickness_source="attribute", spec_deviation=stated.spec_deviation)
    return {
        "name": layer.name,
        "type": layer.type.value if layer.type is not None else "UNKNOWN",
        "context": ref.context.value, "row": ref.row,
        "form": ref.form.value, "form_source": ref.form_source.value,
        "thickness": thickness,
        "copper_index": None if stackup is None else stackup.copper_index(layer.name),
        "copper_span": None if span is None else span.as_dict(),
        "z_mm": None if extent is None else {
            "start_mm": _round(extent.start_mm, digits + 3),
            "end_mm": _round(extent.end_mm, digits + 3),
            "thickness_known": extent.thickness_known, "source": extent.source,
        },
    }


def _layer_data(preview: LayerPreview, digits: int) -> dict:
    rings, _area, _bounds = _serialised_rings(preview, digits)
    return {
        **_layer_metadata(preview.layer, preview.stackup, digits),
        "geometry_status": "ready", "domain_id": None,
        "polygons": [{"island": not ring.is_hole, "points": _points_json(ring, digits)}
                     for ring in rings],
    }


def layer_to_json(preview: LayerPreview, *, round_digits: Optional[int] = None) -> dict:
    """Serialise a single layer through the shared Pydantic IO contract."""
    if preview.drawing_only:
        raise ValueError("a drawing-only layer cannot be exported as intermediate JSON")
    from .intermediate import LayerModel
    return LayerModel.model_validate(_layer_data(preview, _preview_digits(preview, round_digits))).model_dump(
        mode="json", exclude_none=True)


def step_to_document(job: Job, preview: StepPreview, *,
                     round_digits: Optional[int] = None,
                     include_diagnostics: bool = True) -> BoardDocument:
    """Build the current Pydantic contract once at the export boundary.

    Layer geometry may be filtered for a preview, but the entire layer catalog
    remains visible. Failed and intentionally omitted geometry are explicit.
    """
    if preview.drawing_only:
        raise ValueError("a drawing-only preview cannot be exported as intermediate JSON; use the uncut model")
    digits = _preview_digits(preview, round_digits)
    view = regularise_preview(preview, digits, diagnostics=preview.diagnostics)
    domains, domain_of_layer = _domains_json(view, digits)
    composed = {l.name.casefold(): l for l in view.layers}
    failed = {name.casefold() for name in view.failed_layers}
    catalog = {l.name.casefold(): l for l in view.step.layers}
    catalog.update({l.name.casefold(): l.layer for l in view.layers})
    layers = []
    for layer in sorted(catalog.values(), key=lambda l: l.ref.row):
        ready = composed.get(layer.name.casefold())
        if ready is not None:
            data = _layer_data(ready, digits)
        else:
            is_failed = layer.name.casefold() in failed or layer.profile_error is not None
            data = {**_layer_metadata(layer, view.stackup, digits),
                    "geometry_status": "failed" if is_failed else "omitted",
                    "polygons": []}
            if is_failed:
                messages = [d.message for d in view.diagnostics
                            if d.level == "error" and layer.name in d.message]
                data["geometry_error"] = layer.profile_error or (messages[-1] if messages else "layer geometry could not be composed")
        info = domain_of_layer.get(layer.name, {})
        data["domain_id"] = info.get("id")
        if info.get("error") and data.get("geometry_error") is None:
            data["geometry_error"] = info["error"]
        # Validate one layer at a time so the complete untyped coordinate tree
        # does not coexist with a second full Pydantic coordinate tree.
        layers.append(LayerModel.model_validate(data))
    return BoardDocument.model_validate({
        "units": "mm", "resolution_mm": 10 ** -digits,
        "source": {"path": job.source_path, "job_root": job.root,
                   "input_units": preview.units_in,
                   "product": {"name": job.info.job_name or None, "odb_version": job.info.version}},
        "steps": [{"name": preview.step.name,
                   "profile_domain_id": "step_profile" if preview.step.profile is not None else None,
                   "domains": domains, "layers": layers}],
        "diagnostics": [_diagnostic_json(d) for d in preview.diagnostics] if include_diagnostics else None,
    })


def step_to_json(job: Job, preview: StepPreview, *,
                 round_digits: Optional[int] = None,
                 include_diagnostics: bool = True) -> dict:
    """Convenience mapping API; file writers use step_to_document directly."""
    return step_to_document(job, preview, round_digits=round_digits,
                            include_diagnostics=include_diagnostics).model_dump(mode="json", exclude_none=True)


def _region_polygons_json(region, round_digits: int) -> list[dict]:
    """Serialise a :class:`~odbpp.clipper.Region` as explicit island/hole rings."""
    return [
        {
            "island": not ring.is_hole,
            "points": _points_json(ring, round_digits),
        }
        for ring in region.rings
    ]


def _domains_json(preview: StepPreview, round_digits: int) -> tuple[list[dict], dict]:
    """The computational domains available to this step, and which layer uses which.

    AGENTS constraint 7: a material-equivalence consumer needs each physical
    layer's two-dimensional calculation domain separately from its material
    *pattern*.  The copper polygons are a pattern -- where the metal is -- and
    they say nothing about where the layer exists at all.  This array is that
    domain, resolved with the constraint 3 priority rule:

    * a valid layer profile wins (``source: layer_profile``);
    * otherwise the step profile applies (``source: step_profile``);
    * a layer profile that exists but cannot be parsed is *not* silently
      replaced by the step profile: it sets ``geometry_error`` and leaves
      ``domain_id`` null.

    The step profile is emitted once and shared by every layer that falls back to
    it, rather than copied per layer, so a 43-layer rigid-flex board does not
    repeat the same polygon set 40 times.

    Returns ``(domains, mapping)`` where *mapping* is keyed by layer name.
    """
    from .clipper import region_from_geometry

    domains: list[dict] = []
    mapping: dict[str, dict] = {}
    seen: dict[int, str] = {}

    step = preview.step
    step_domain_id: Optional[str] = None
    if step.profile is not None:
        step_domain_id = "step_profile"
        region, collapsed = snap_to_grid(
            region_from_geometry(step.profile), round_digits
        )
        if collapsed:
            preview.diagnostics.warn(
                EXPORT_DEGENERATE_RING,
                f"step {step.name!r} profile: {collapsed} ring(s) had no area left at "
                f"the {round_digits}-decimal output grid and were dropped; "
                f"the remaining geometry was re-regularised at that resolution",
            )
        domains.append(_domain_json(step_domain_id, "step_profile",
                                    region, round_digits))

    for layer in preview.step.layers:
        if layer.profile_error is not None:
            mapping[layer.name] = {
                "id": None,
                "source": None,
                "error": f"layer profile could not be parsed: {layer.profile_error}",
            }
            continue
        if layer.profile is not None:
            # Two layers sharing one profile object (rare, but a step can be
            # authored that way) get one entry keyed by object identity.
            key = id(layer.profile)
            domain_id = seen.get(key)
            if domain_id is None:
                domain_id = f"layer_profile:{layer.name}"
                region, _collapsed = snap_to_grid(
                    region_from_geometry(layer.profile), round_digits
                )
                domains.append(_domain_json(domain_id, "layer_profile", region, round_digits))
                seen[key] = domain_id
            mapping[layer.name] = {"id": domain_id, "source": "layer_profile",
                                   "error": None}
            continue
        if step_domain_id is not None:
            mapping[layer.name] = {"id": step_domain_id, "source": "step_profile",
                                   "error": None}
            continue
        mapping[layer.name] = {
            "id": None,
            "source": None,
            "error": "neither a layer profile nor a step profile is available",
        }

    return domains, mapping


def _domain_json(domain_id: str, source: str, region, round_digits: int) -> dict:
    return {"id": domain_id, "source": source,
            "polygons": _region_polygons_json(region, round_digits)}


def _diagnostic_json(diagnostic: Diagnostic) -> dict:
    return {
        "level": diagnostic.level,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": diagnostic.path,
    }


def write_json(document: dict, path: str) -> None:
    """Validate and write through the single current contract."""
    from .intermediate import validate_document, write_document
    write_document(validate_document(document), path)


def layer_summary_lines(preview: StepPreview) -> str:
    """A one-line-per-layer text summary, used by the CLI and tests."""
    lines = []
    for layer in preview.layers:
        bounds = layer.bounds
        where = "" if bounds is None else \
            f" [{bounds[0]:.2f},{bounds[1]:.2f} .. {bounds[2]:.2f},{bounds[3]:.2f}]"
        lines.append(
            f"{layer.name:<26} {layer.type_name:<14} "
            f"rings={len(layer.rings):<6} area={layer.area_mm2:>12.3f} mm2"
            f"{' base' if layer.geometry.used_base else ''}{where}"
        )
    return "\n".join(lines)
