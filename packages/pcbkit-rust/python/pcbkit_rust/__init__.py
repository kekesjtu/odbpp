"""ODB++ parser, polygon normalisation and vector previews."""
from .archive import OdbArchive
from .diagnostics import Diagnostic, Diagnostics
from .errors import (
    ArchiveError,
    LayerProfileError,
    OdbError,
    ParseError,
    SymbolCycleError,
    UnsupportedFeatureError,
)
from .clipper import Region, Ring, difference, intersection, union
from .expand import ExpandOptions, Expander, LayerGeometry, expand_layer
from .export import (
    PreviewOptions,
    StepPreview,
    build_step_preview,
    layer_to_json,
    regularise_preview,
    step_to_json,
    step_to_document,
)
from .intermediate import BoardDocument, read_document, write_document
from .job import LoadOptions, load_job
from .stackup import Stackup, ZExtent, ZSpan
from .model import (
    Job,
    Layer,
    LayerRef,
    LayerType,
    Matrix,
    Polarity,
    Step,
)
from .symbols import SymbolLibrary, is_standard_symbol, parse_standard_symbol
from .svg import SvgOptions, render_html, render_svg, write_outputs
from .units import UnitScope, UnitSystem, copper_weight
from .units import DEFAULT_COPPER_DENSITY_G_CM3

__version__ = "0.1.0"
__all__ = ['OdbArchive', 'Diagnostic', 'Diagnostics', 'ArchiveError', 'LayerProfileError', 'OdbError', 'ParseError', 'SymbolCycleError', 'UnsupportedFeatureError', 'Region', 'Ring', 'difference', 'intersection', 'union', 'ExpandOptions', 'Expander', 'LayerGeometry', 'expand_layer', 'PreviewOptions', 'StepPreview', 'build_step_preview', 'layer_to_json', 'regularise_preview', 'step_to_json', 'step_to_document', 'BoardDocument', 'read_document', 'write_document', 'LoadOptions', 'load_job', 'Stackup', 'ZExtent', 'ZSpan', 'Job', 'Layer', 'LayerRef', 'LayerType', 'Matrix', 'Polarity', 'Step', 'SymbolLibrary', 'is_standard_symbol', 'parse_standard_symbol', 'SvgOptions', 'render_html', 'render_svg', 'write_outputs', 'UnitScope', 'UnitSystem', 'copper_weight', 'DEFAULT_COPPER_DENSITY_G_CM3']
