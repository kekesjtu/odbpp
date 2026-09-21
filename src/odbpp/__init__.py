"""ODB++ v8.1 parser and geometry normaliser.

Reads an ODB++ job (exploded directory, ``.tgz``/``.tar.gz`` or ``.zip``) and
produces a resolved, all-positive, millimetre-only polygon model.

Quick start::

    from odbpp import load_job

    job = load_job("cases/kicad-video-odb.tgz")
    step = job.step("board")
    for layer in step.layers:
        print(layer.name, layer.type, layer.feature_count)

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
from .job import LoadOptions, load_job
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
from .units import UnitScope, UnitSystem, copper_weight

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
    # symbols
    "SymbolLibrary",
    "is_standard_symbol",
    "parse_standard_symbol",
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
