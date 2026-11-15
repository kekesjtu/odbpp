"""Rendering-only dielectric substrates and drill cutouts."""
from dataclasses import replace

from .clipper import difference, snap_to_grid, union
from .expand import Expander, expand_layer
from .export import StepPreview, _preview_digits
from .stackup import Stackup


def dielectric_drill_cutouts(preview: StepPreview) -> StepPreview:
    """Build dielectric substrates and cut resolved drills in a drawing copy.

    Copper objects are returned unchanged. Dielectric profiles seed the drawing,
    with their holes and the layer's artwork applied before drills. Every DRILL
    layer of the board is already composed, so a cutout never has to reach back
    into the input model.
    Projection and union caches live only during this render, never across input.
    """
    if preview.drawing_only or not any(l.type_name == "DIELECTRIC" and l.layer.ref.context.value == "BOARD"
                                      for l in preview.layers):
        return preview
    digits = _preview_digits(preview, None)
    stackup = preview.stackup or Stackup(preview.step, diagnostics=preview.diagnostics)
    expander = Expander(preview.source_symbols, options=preview.expansion_options,
                        diagnostics=preview.diagnostics)
    projections = {l.name: l.geometry.region for l in preview.layers if l.type_name == "DRILL"}
    unions = {}
    layers = []
    for layer in preview.layers:
        if layer.type_name != "DIELECTRIC" or layer.layer.ref.context.value != "BOARD":
            layers.append(layer)
            continue
        geometry = expand_layer(layer.layer, preview.step, expander=expander,
                                round_digits=digits, with_dielectric_base=True)
        names = [drill.name for drill in stackup.drills_through(layer.layer)
                 if projections[drill.name]]
        if not names:
            layers.append(replace(layer, geometry=geometry, drawing_only=True))
            continue
        key = tuple(names)
        if key not in unions:
            unions[key] = union(*(projections[name] for name in names))
        base = geometry.region
        region = difference(base, unions[key])
        removed = max(0.0, base.area - region.area)
        region, collapsed = snap_to_grid(region, digits)
        if collapsed:
            preview.diagnostics.warn("drawing.degenerate_ring",
                f"dielectric {layer.name!r}: {collapsed} cutout ring(s) collapsed at {digits} decimal places")
        geometry = replace(geometry, region=region,
                           drills_subtracted=names, drill_cut_mm2=removed)
        layers.append(replace(layer, geometry=geometry, drawing_only=True))
    return replace(preview, layers=layers, drawing_only=True,
                   empty_layers=[l.name for l in layers if l.is_empty])
