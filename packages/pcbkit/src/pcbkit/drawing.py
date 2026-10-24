"""Rendering-only dielectric cutouts; the shared JSON geometry stays untouched."""
from dataclasses import replace

from .clipper import difference, snap_to_grid, union
from .expand import Expander, expand_layer
from .export import StepPreview, _preview_digits
from .stackup import Stackup


def dielectric_drill_cutouts(preview: StepPreview, layer_names: set[str] | None = None) -> StepPreview:
    """Create a drawing copy, cutting only resolved drills crossing dielectric.

    Copper objects are returned unchanged. Profile holes and each layer's own
    resolved artwork remain in the base. A filtered preview can still obtain a
    drill from the loaded step, using the original symbols and expansion options.
    Projection and union caches live only during this render, never across input.
    """
    if preview.drawing_only or not any(l.type_name == "DIELECTRIC" and l.layer.ref.context.value == "BOARD"
                                      and (layer_names is None or l.name in layer_names)
                                      for l in preview.layers):
        return preview
    digits = _preview_digits(preview, None)
    stackup = preview.stackup or Stackup(preview.step, diagnostics=preview.diagnostics)
    expander = Expander(preview.source_symbols, options=preview.expansion_options,
                        diagnostics=preview.diagnostics)
    projections = {l.name: l.geometry.region for l in preview.layers if l.type_name == "DRILL"}
    failed = set(preview.failed_layers)
    unions = {}
    layers = []
    for layer in preview.layers:
        if (layer.type_name != "DIELECTRIC" or layer.layer.ref.context.value != "BOARD"
                or (layer_names is not None and layer.name not in layer_names)):
            layers.append(layer)
            continue
        names = []
        for drill in stackup.drills_through(layer.layer):
            if drill.name in failed:
                preview.diagnostics.error("drawing.drill_failed",
                    f"dielectric {layer.name!r}: drill {drill.name!r} has failed geometry; its cutout cannot be drawn")
                continue
            if drill.name not in projections:
                # An omitted DRILL layer still affects a selected dielectric SVG.
                try:
                    geometry = expand_layer(drill, preview.step, expander=expander)
                    projections[drill.name], _ = snap_to_grid(geometry.region, digits)
                except Exception as exc:  # same per-layer degradation as the preview builder
                    failed.add(drill.name)
                    preview.diagnostics.error("drawing.drill_failed",
                        f"dielectric {layer.name!r}: drill {drill.name!r} cannot be drawn: {type(exc).__name__}: {exc}")
                    continue
            if projections[drill.name]:
                names.append(drill.name)
        if not names:
            layers.append(layer)
            continue
        key = tuple(names)
        if key not in unions:
            unions[key] = union(*(projections[name] for name in names))
        base = layer.geometry.region
        region = difference(base, unions[key])
        removed = max(0.0, base.area - region.area)
        region, collapsed = snap_to_grid(region, digits)
        if collapsed:
            preview.diagnostics.warn("drawing.degenerate_ring",
                f"dielectric {layer.name!r}: {collapsed} cutout ring(s) collapsed at {digits} decimal places")
        geometry = replace(layer.geometry, region=region,
                           drills_subtracted=names, drill_cut_mm2=removed)
        layers.append(replace(layer, geometry=geometry, drawing_only=True))
    return replace(preview, layers=layers, drawing_only=True,
                   empty_layers=[l.name for l in layers if l.is_empty])
