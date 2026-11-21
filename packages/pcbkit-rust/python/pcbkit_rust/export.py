"""Native BOARD regions and independent MISC drawings for export and preview.

BOARD geometry is regularised in Rust and shared by the JSON contract and
renderers. MISC uses ordered paint handles only. Profile geometry is quantised,
deduplicated and assigned to layers at the contract boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Optional
from .clipper import Region, Ring, layer_profile_region, region_from_geometry, snap_to_grid
from .diagnostics import (
    EXPORT_DEGENERATE_RING,
    EXPORT_LAYER_FAILED,
    EXPORT_NO_GEOMETRY,
    Diagnostics,
)
from .errors import LayerGeometryError, LayerProfileError
from .expand import ExpandOptions, Expander, LayerGeometry, expand_layer
from .geometry import TessellationOptions
from .model import Job, Layer, LayerType, Step
from .stackup import Stackup
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
    include_misc: bool = True
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
    """One BOARD region or preview-only MISC drawing."""

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
    drawing: Optional[object] = field(default=None, repr=False, compare=False)

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
        return self.drawing.bounds if self.drawing is not None else self.geometry.bounds

    @property
    def is_empty(self) -> bool:
        return not self.drawing if self.drawing is not None else not self.geometry.region

    def layer_attributes(self) -> dict[str, str]:
        """Layer-level attributes worth surfacing, per requirements.md thickness-unit rules.

        ``.layer_dielectric`` is a *Layer*-class field and is meaningful only for
        non-copper layers; ``.copper_weight`` changes physical meaning with the
        unit system (spec p.24), so both are reported as authored here.  The
        *interpreted* values are separate fields -- see
        :meth:`normalised_attributes` -- because a single number cannot carry
        both the raw file text and the millimetre value without inviting
        exactly the confusion requirements.md thickness-unit rules warns about.
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
        """Interpreted ``.layer_dielectric`` as a :class:`~pcbkit.stackup.Thickness`."""
        if self.stackup is None:
            return None
        return self.stackup.thickness(self.layer)

    def copper_weight(self):
        """Interpreted ``.copper_weight``; a density in imperial, a thickness in metric."""
        if self.stackup is None:
            return None
        return self.stackup.weight(self.layer)

    def z_extent(self):
        """Absolute millimetre Z extent (:class:`~pcbkit.stackup.ZExtent`).

        ``None`` when the preview has no stack model, or when the layer is not
        part of the board (documentation, component outlines).  See
        :meth:`StepPreview.z_axis` for the axis convention.
        """
        if self.stackup is None:
            return None
        return self.stackup.z_extent(self.layer)


@dataclass
class StepPreview:
    """A step with BOARD regions, MISC drawings and stackup context."""

    step: Step
    layers: list[LayerPreview]
    units_in: str
    diagnostics: Diagnostics
    #: Layers that composed successfully to zero area -- usually intentional, and
    #: most often zero-width (``r0``) artwork.  Every BOARD layer in the document
    #: carries geometry, so this is a property of the board, not a gap.
    empty_layers: list[str] = field(default_factory=list)
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
    expansion_options: ExpandOptions = field(default_factory=ExpandOptions, repr=False, compare=False)
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

    def has_geometry(self) -> bool:
        """Empty dielectric artwork still has a physical profile to export."""
        return bool(self.non_empty()) or any(
            layer_profile_region(self.step, layer.layer, self.expansion_options.tessellation)
            for layer in self.layers if layer.type_name == "DIELECTRIC"
        )


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

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

    A layer that cannot be composed raises instead of being dropped: dropping
    it would leave the document claiming to describe a board it does not.
    """
    opts = options or PreviewOptions()
    diags = diagnostics if diagnostics is not None else Diagnostics()
    expander = expander or _make_expander(job, opts, diags)
    # One stack model per step; its spans are exported and used later by the
    # dielectric renderer. Cross-layer drills do not modify parser geometry.
    stackup = expander.stackup(step)

    for layer in step.layers:
        if layer.ref.context.value != "BOARD" and not opts.include_misc:
            continue
        if layer.features is None and layer.profile is None and step.profile is None:
            if layer.ref.context.value == "MISC":
                continue
            # A layer with no artwork is a layer whose pattern is empty, not a
            # layer missing from the board.
            yield LayerPreview(layer=layer, geometry=LayerGeometry(layer.name, Region(), 0),
                               stackup=stackup, round_digits=opts.round_digits)
            continue
        try:
            if layer.ref.context.value == "MISC":
                from .features import collect_diagnostics
                seed = layer.is_negative or layer.type is LayerType.DIELECTRIC
                if layer.profile_error is not None:
                    step.base_geometry(layer)
                drawing = expander.native.drawing(layer.features, layer_profile_region(step, layer, opts.tessellation) if seed else None,
                                                   layer.is_negative, seed,
                                                   variables=expander.text_variables(step, layer))
                collect_diagnostics(expander.native, diags)
                yield LayerPreview(layer=layer, geometry=LayerGeometry(layer.name, Region(), layer.feature_count),
                                   stackup=stackup, round_digits=opts.round_digits, drawing=drawing)
                continue
            geometry = expand_layer(layer, step, expander=expander,
                                    round_digits=opts.round_digits)
        except Exception as exc:  # noqa: BLE001 - the first bad layer stops the run
            # requirements.md parser constraint 3: a layer profile that exists but is unusable is
            # a hard error, and so is any other failed composition.  The document
            # describes one complete board, so it is not written at all rather
            # than carrying a hole the consumer cannot see.
            reason = (exc.reason if isinstance(exc, LayerProfileError)
                      else f"{type(exc).__name__}: {exc}")
            diags.error(EXPORT_LAYER_FAILED,
                        f"layer {layer.name!r} could not be composed: {reason}",
                        layer.dir_name or None)
            raise LayerGeometryError(layer.name, reason) from exc

        # Quantise to the output grid and re-validate *before* the geometry is
        # called a result.  Areas and bounds below are recomputed from these
        # coordinates, so what the JSON reports is what the JSON contains.
        collapsed = geometry.collapsed
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
        if layer.drawing is not None:
            layers.append(replace(layer, round_digits=digits))
            continue
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
    """Prepare native BOARD regions and MISC drawings for one step."""
    opts = options or PreviewOptions()
    diags = diagnostics if diagnostics is not None else (
        job.diagnostics if isinstance(job.diagnostics, Diagnostics) else Diagnostics()
    )
    step = step or job.single_step

    expander = _make_expander(job, opts, diags)
    stackup = expander.stackup(step)
    layers = list(iter_step_previews(job, step, options=opts,
                                     diagnostics=diags, expander=expander))

    preview = StepPreview(
        step=step,
        layers=layers,
        units_in=job.units.system.value,
        diagnostics=diags,
        empty_layers=[l.name for l in layers if l.is_empty],
        stackup=stackup,
        round_digits=opts.round_digits,
        source_symbols=job.symbols,
        expansion_options=opts.expand_options(),
    )
    if not preview.has_geometry():
        diags.error(
            EXPORT_NO_GEOMETRY,
            f"every BOARD layer of step {step.name!r} composed to an empty "
            f"region ({len(layers)} layer(s))",
        )
    return preview


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _round(value: float, digits: int) -> float:
    out = round(float(value) + 0.0, digits)
    return 0.0 if out == 0.0 else out


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


def _layer_metadata(layer: Layer, stackup: Optional[Stackup], digits: int) -> dict:
    """One authoritative layer record; Z reports each known half by itself."""
    ref = layer.ref
    # DRILL dimensions are derived from its endpoint copper layers.
    # Keep the derived extent inside Stackup for previews, not in the interface.
    extent = None if stackup is None or layer.type is LayerType.DRILL else stackup.z_extent(layer)
    endpoints = stackup.drill_endpoints(layer) if stackup is not None and layer.type is LayerType.DRILL else None
    return {
        "name": layer.name,
        "type": layer.type.value if layer.type is not None else "UNKNOWN",
        "context": ref.context.value, "row": ref.row,
        "start_layer": None if endpoints is None else endpoints[0].name,
        "end_layer": None if endpoints is None else endpoints[1].name,
        "z_mm": None if extent is None else {
            "start_mm": _round(extent.start_mm, digits + 3) if extent.position_known else None,
            "thickness_mm": _round(extent.thickness_mm, digits + 3) if extent.thickness_stated else None,
        },
    }


def _layer_data(preview: LayerPreview, digits: int) -> dict:
    region = preview.geometry.region
    if preview.round_digits != digits:
        region, _ = snap_to_grid(region, digits)
    return {
        **_layer_metadata(preview.layer, preview.stackup, digits),
        "polygons": region.polygons(),
    }


def layer_to_json(preview: LayerPreview, *, round_digits: Optional[int] = None) -> dict:
    """Serialise a single layer through the shared Pydantic IO contract."""
    if preview.drawing_only or preview.layer.ref.context.value != "BOARD":
        raise ValueError("a drawing-only layer cannot be exported as intermediate JSON")
    from .intermediate import LayerModel
    return LayerModel.model_validate(_layer_data(preview, _preview_digits(preview, round_digits))).model_dump(
        mode="json", exclude_none=True)


def step_to_document(job: Job, preview: StepPreview, *,
                     round_digits: Optional[int] = None) -> BoardDocument:
    """Build the current Pydantic contract once at the export boundary.

    The catalogue is the board: every BOARD layer of the step appears exactly
    once, with the geometry composed for it.  MISC drawings are preview-only.
    """
    if preview.drawing_only:
        raise ValueError("a drawing-only preview cannot be exported as intermediate JSON; use the uncut model")
    digits = _preview_digits(preview, round_digits)
    view = regularise_preview(preview, digits, diagnostics=preview.diagnostics)
    profiles, profile_id = _profiles_json(view, digits)
    # Validate one layer at a time so the untyped coordinate tree and the
    # validated one never both hold every polygon of the board.
    layers = [LayerModel.model_validate(_layer_data(layer, digits))
              for layer in sorted(view.layers, key=lambda l: l.layer.ref.row)
              if layer.layer.ref.context.value == "BOARD"]
    return BoardDocument.model_validate({
        "units": "mm", "resolution_mm": 10 ** -digits,
        "path": job.source_path,
        "steps": [{"name": preview.step.name,
                   "profile_id": profile_id,
                   "profiles": profiles, "layers": layers}],
    })


def step_to_json(job: Job, preview: StepPreview, *,
                 round_digits: Optional[int] = None) -> dict:
    """Convenience mapping API; file writers use step_to_document directly."""
    return step_to_document(job, preview, round_digits=round_digits).model_dump(mode="json", exclude_none=True)


def _profiles_json(preview: StepPreview, round_digits: int) -> tuple[list[dict], Optional[str]]:
    """Export each quantised profile geometry once, with its layer membership."""
    profiles, regions = [], []
    cached = {}

    def register(profile, inherit_step_holes=False):
        key = (id(profile), inherit_step_holes)
        if key in cached:
            return cached[key]
        tessellation = preview.expansion_options.tessellation
        region = profile.layer_region(preview.step.profile, tessellation.sagitta_mm, tessellation.max_segments) if inherit_step_holes else region_from_geometry(profile, tessellation)
        region, _ = snap_to_grid(region, round_digits)
        for index, existing in enumerate(regions):
            if region.same_geometry(existing):
                cached[key] = index
                return index
        index = len(profiles)
        regions.append(region)
        profiles.append({"id": f"profile:{index}", "layers": [], "polygons": region.polygons()})
        cached[key] = index
        return index

    step_id = None
    if preview.step.profile is not None:
        step_id = profiles[register(preview.step.profile)]["id"]
    for layer in preview.step.layers:
        if layer.ref.context.value != "BOARD":
            continue
        profile = layer.profile if layer.profile is not None else preview.step.profile
        if profile is None:
            if layer.is_copper or layer.type is LayerType.DIELECTRIC:
                raise LayerGeometryError(
                    layer.name,
                    "neither a layer profile nor a step profile is available",
                )
            continue
        profiles[register(profile, layer.profile is not None)]["layers"].append(layer.name)
    return profiles, step_id


def write_json(document: dict, path: str) -> None:
    """Validate and write through the single current contract."""
    from .intermediate import validate_document, write_document
    write_document(validate_document(document), path)


def layer_summary_lines(preview: StepPreview) -> str:
    """A one-line-per-layer text summary, used by the CLI and tests."""
    lines = []
    for layer in preview.layers:
        if layer.drawing is not None:
            lines.append(f"{layer.name:<26} {layer.type_name:<14} preview only, {layer.geometry.records} input features")
            continue
        bounds = layer.bounds
        where = "" if bounds is None else \
            f" [{bounds[0]:.2f},{bounds[1]:.2f} .. {bounds[2]:.2f},{bounds[3]:.2f}]"
        lines.append(
            f"{layer.name:<26} {layer.type_name:<14} "
            f"rings={len(layer.rings):<6} area={layer.area_mm2:>12.3f} mm2"
            f"{' base' if layer.geometry.used_base else ''}{where}"
        )
    return "\n".join(lines)
