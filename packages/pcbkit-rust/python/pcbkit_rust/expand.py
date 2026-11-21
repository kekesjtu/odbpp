"""Adapt layer metadata to the native whole-layer composer."""
from dataclasses import dataclass, field

from ._pcbkit_native import Composer, Region
from .features import collect_diagnostics
from .clipper import layer_profile_region
from .geometry import TessellationOptions
from .model import LayerType
from .stackup import Stackup


@dataclass(frozen=True)
class ExpandOptions:
    tessellation: TessellationOptions = field(default_factory=TessellationOptions)
    max_symbol_depth: int = 16
    warn_unsupported_strokes: bool = True
    minimum_stroke_mm: float = 0.0


@dataclass
class LayerGeometry:
    layer_name: str
    region: Region
    records: int = 0
    polarity_runs: int = 0
    used_base: bool = False
    collapsed: int = 0
    drills_subtracted: list[str] = field(default_factory=list)
    drill_cut_mm2: float = 0.0

    @property
    def area_mm2(self):
        return self.region.area

    @property
    def bounds(self):
        return self.region.bounds

    @property
    def polygon_count(self):
        return len(self.region)


class Expander:
    def __init__(self, library=None, *, options=None, diagnostics=None):
        from .diagnostics import Diagnostics
        self.options = options or ExpandOptions()
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics()
        opts = self.options
        self.native = Composer(library.files if library else {}, opts.tessellation.sagitta_mm,
            opts.tessellation.max_segments, opts.minimum_stroke_mm,
            opts.max_symbol_depth, opts.warn_unsupported_strokes,
            fonts=library.fonts if library else None)
        self._text_variables = library.text_variables if library else {}
        self._stackups = {}

    def text_variables(self, step, layer):
        return dict(self._text_variables, STEP=step.name, LAYER=layer.name)

    def stackup(self, step):
        if step.name not in self._stackups:
            self._stackups[step.name] = Stackup(step, diagnostics=self.diagnostics)
        return self._stackups[step.name]


def expand_layer(layer, step, *, expander, round_digits=4, with_dielectric_base=False):
    """Compose artwork; dielectric substrate is requested only for rendering."""
    dielectric = layer.type is LayerType.DIELECTRIC
    seed = with_dielectric_base if dielectric else layer.is_negative
    # A declared but invalid profile fails this layer even if it starts empty.
    if layer.profile_error is not None:
        step.base_geometry(layer)
    profile = layer_profile_region(step, layer, expander.options.tessellation) if seed or dielectric else None
    try:
        region, runs, collapsed = expander.native.layer(layer.features, profile,
            layer.is_negative, seed, round_digits, variables=expander.text_variables(step, layer))
    finally:
        collect_diagnostics(expander.native, expander.diagnostics)
    if not region and (not dielectric or layer.feature_count):
        expander.diagnostics.warn("geometry.empty_result", f"layer {layer.name!r} composed to an empty region", layer.dir_name or None)
    return LayerGeometry(layer.name, region, layer.feature_count, runs, seed and bool(profile), collapsed)
