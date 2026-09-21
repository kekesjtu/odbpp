"""ODB++ v8.1 parser and geometry normaliser.

Reads an ODB++ job (exploded directory, ``.tgz``/``.tar.gz`` or ``.zip``) and
produces a resolved, all-positive, millimetre-only polygon model.  The pipeline
has three stages, each usable on its own:

1. **Parse** -- :func:`load_job` reads the job into a
   :class:`~odbpp.model.Job`.  This is file-level: units resolved, matrix
   honoured, every record typed, nothing dropped silently.
2. **Compose** -- :func:`build_step_preview` expands symbols, sweeps lines and
   arcs, and resolves polarity into solid regions per layer.
3. **Render** -- :func:`render_svg` / :func:`render_html` / :func:`write_outputs`
   draw them, or take the composed polygons as JSON via
   :func:`step_to_json`.

Quick start::

    from odbpp import build_step_preview, load_job, write_outputs

    job = load_job("cases/kicad-video-odb.tgz")
    preview = build_step_preview(job)
    write_outputs(preview, job, "out/kicad-video")

Design notes that explain the shape of the API:

* **Units are never implicit.**  ODB++ distinguishes *coordinate* units
  (``mm``/``inch``) from *size* units (``µm``/``mil``), and mixing them is a
  1000x error.  :class:`~odbpp.units.UnitScope` therefore exposes
  ``coord_mm()`` and ``size_mm()`` separately and no generic converter exists.
* **Everything downstream is millimetres** (AGENTS constraint 4).
* **The matrix is authoritative** for the layer list; unregistered layer
  directories are reported and ignored.
* **Warnings are data.**  Anything dropped or repaired is recorded in
  :class:`~odbpp.diagnostics.Diagnostics` with a stable code.
* **Orientation is data too.**  Every ring that comes out is wound to the spec
  convention (islands clockwise, holes counter-clockwise, p.39), so a consumer
  never has to infer nesting from a winding it cannot verify.
* **Nothing is approximated.**  A line or arc is a Minkowski sweep of its symbol
  (spec p.38), not a thick pen stroke, and overlapping positive features union
  rather than cancelling.  Both need real boolean geometry, which is why
  :mod:`odbpp.clipper` exists.
"""

from __future__ import annotations

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
)
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

__all__ = [
    "__version__",
    # loading
    "load_job",
    "LoadOptions",
    "OdbArchive",
    # model
    "Job",
    "Step",
    "Layer",
    "LayerRef",
    "LayerType",
    "Matrix",
    "Polarity",
    # units
    "UnitScope",
    "UnitSystem",
    "copper_weight",
    "DEFAULT_COPPER_DENSITY_G_CM3",
    # symbols
    "SymbolLibrary",
    "is_standard_symbol",
    "parse_standard_symbol",
    # geometry (stage 2)
    "ExpandOptions",
    "Expander",
    "LayerGeometry",
    "expand_layer",
    "Region",
    "Ring",
    "union",
    "difference",
    "intersection",
    "PreviewOptions",
    "StepPreview",
    "build_step_preview",
    "regularise_preview",
    "step_to_json",
    "layer_to_json",
    # stackup (layer order, dielectric/drill Z spans, thicknesses)
    "Stackup",
    "ZSpan",
    "ZExtent",
    # rendering (stage 3)
    "SvgOptions",
    "render_svg",
    "render_html",
    "write_outputs",
    # diagnostics
    "Diagnostic",
    "Diagnostics",
    # errors
    "OdbError",
    "ArchiveError",
    "ParseError",
    "SymbolCycleError",
    "LayerProfileError",
    "UnsupportedFeatureError",
]
