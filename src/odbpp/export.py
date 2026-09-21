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

from dataclasses import dataclass, field
from typing import Iterator, Optional, Sequence
from .clipper import Ring
from .diagnostics import (
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
from .model import Job, Layer, Polygon, Step


__all__ = [
    "SCHEMA",
    "PreviewOptions",
    "LayerPreview",
    "StepPreview",
    "build_step_preview",
    "iter_step_previews",
    "layer_to_json",
    "step_to_json",
    "layer_summary_lines",
    "write_json",
]

#: Identifies the JSON layout, so a consumer can reject a newer file rather than
#: misread it.
SCHEMA = "odbpp/preview-1"


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
    #: Decimal places for coordinates in the JSON and SVG.  4 is 0.1 um, far
    #: finer than any fabrication tolerance and much smaller on disk.
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
        unit system (spec p.24), so both are reported as authored and left for
        the consumer to interpret.
        """
        wanted = (".layer_dielectric", ".copper_weight", ".layer_sequence",
                  ".dielectric_name", ".polarity")
        out: dict[str, str] = {}
        for key, value in self.layer.attributes.items():
            if key.casefold() in wanted:
                out[key] = value
        return out


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

    @property
    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        boxes = [l.bounds for l in self.layers if l.bounds is not None]
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
        yield LayerPreview(layer=layer, geometry=geometry)


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
    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _round(value: float, digits: int) -> float:
    out = round(float(value) + 0.0, digits)
    return 0.0 if out == 0.0 else out


def _points_json(ring: Ring, digits: int) -> list[list[float]]:
    return [[_round(x, digits), _round(y, digits)] for x, y in ring.points]


def layer_to_json(preview: LayerPreview, *, round_digits: int = 4) -> dict:
    """One layer as JSON-ready data.

    Rings are emitted as flat point lists with an explicit ``island`` flag rather
    than as path strings, so a consumer can do geometry with them.  Nesting is
    *stated* by the flag and not inferred from winding, even though the winding
    is also correct.
    """
    geometry = preview.geometry
    bounds = preview.bounds

    return {
        "name": preview.name,
        "type": preview.type_name,
        "context": preview.layer.ref.context.value,
        "polarity": preview.layer.ref.polarity.value,
        "form": preview.layer.ref.form.value,
        # How firm that answer is. Spec p.66 defaults FORM to RIGID, so is_flex is
        # always a real boolean; form_source says whether the exporter declared
        # it, a FLEX subtype implied it, or the RIGID default supplied it.
        "form_source": preview.layer.ref.form_source.value,
        "is_flex": preview.layer.ref.is_flex,
        "is_copper": preview.is_copper,
        "row": preview.layer.ref.row,
        "attributes": preview.layer_attributes(),
        "base_from_profile": geometry.used_base,
        "feature_records": geometry.records,
        "polarity_runs": geometry.polarity_runs,
        "area_mm2": _round(preview.area_mm2, round_digits + 2),
        "bounds_mm": None if bounds is None
        else [_round(b, round_digits) for b in bounds],
        "polygons": [
            {
                "island": not ring.is_hole,
                "points": _points_json(ring, round_digits),
            }
            for ring in preview.rings
        ],
    }


def step_to_json(job: Job, preview: StepPreview, *,
                 round_digits: int = 4,
                 include_diagnostics: bool = True) -> dict:
    """The whole intermediate document (data-flow step 2)."""
    from . import __version__

    bounds = preview.bounds
    document = {
        "schema": SCHEMA,
        "generator": {"name": "odbpp", "version": __version__},
        "units": "mm",
        "source": {
            "path": job.source_path,
            "job_root": job.root,
            "input_units": preview.units_in,
            "product": {
                "name": job.info.job_name or None,
                "version": job.info.version,
                "odb_version": job.info.version,
            },
        },
        "coordinate_system": {
            "origin": "as authored in the ODB++ step (no recentring)",
            "y_axis": "up",
            "winding": "islands clockwise, holes counter-clockwise (spec p.39)",
            "resolution_mm": 10 ** -round_digits,
        },
        "steps": [{
            "name": preview.step.name,
            "bounds_mm": None if bounds is None
            else [_round(b, round_digits) for b in bounds],
            "profile": _profile_json(preview.step, round_digits),
            "layers": [layer_to_json(l, round_digits=round_digits)
                       for l in preview.layers],
        }],
        "summary": {
            "layer_count": len(preview.layers),
            "polygon_count": preview.total_polygons,
            "empty_layers": preview.empty_layers,
            "failed_layers": preview.failed_layers,
        },
    }

    if include_diagnostics:
        document["diagnostics"] = [_diagnostic_json(d) for d in preview.diagnostics]

    return document


def _profile_json(step: Step, round_digits: int) -> Optional[dict]:
    if step.profile is None:
        return None
    from .geometry import geometry_points

    polygons = geometry_points(step.profile)
    return {
        "polygons": [
            {
                "island": polygon.is_island,
                "points": [[_round(x, round_digits), _round(y, round_digits)]
                           for x, y in _polygon_ring(polygon)],
            }
            for polygon in polygons
        ]
    }


def _polygon_ring(polygon: Polygon) -> list[tuple[float, float]]:
    points = list(polygon.points())
    while len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _diagnostic_json(diagnostic: Diagnostic) -> dict:
    return {
        "level": diagnostic.level,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": diagnostic.path,
    }


def write_json(document: dict, path: str) -> None:
    """Write the document with stable formatting."""
    import json

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")


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
