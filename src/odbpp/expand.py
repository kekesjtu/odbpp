"""Feature expansion: turning parsed records into solid millimetre regions.

The parser in :mod:`odbpp.features` answers *what does the file say*.  This
module answers *what area of the board does it cover*, which needs four things
the file itself does not provide:

1. **Symbol resolution.**  A record names a symbol; a standard name expands
   arithmetically (Appendix A), a user-defined name has to be loaded from
   ``symbols/`` and itself expanded, recursively.  A record whose symbol cannot
   be resolved contributes nothing and is reported.

2. **Placement.**  Pads are rotated and then mirrored (spec p.34-35), then
   translated.  Rotation happens before mirroring, and both happen around the
   symbol origin.

3. **Sweeps.**  ``L`` and ``A`` records *drag* a symbol along a segment or arc
   (spec p.38).  That is a Minkowski sum, not a thick pen stroke -- see
   :mod:`odbpp.clipper`.

4. **Polarity.**  Records are drawn in file order, positives adding and
   negatives erasing (spec p.39).  The result must be a fully positive polygon
   set (AGENTS constraint 2).

Batching
--------
A production board has hundreds of thousands of records (``designodb-rigidflex``
has 471,158 ``L`` records in one job), so expanding one record per boolean
operation is not viable.  Two batching rules are applied, both chosen so the
result is *identical* to the naive in-order expansion:

* The file is split into maximal runs of equal **effective polarity**.  Records
  inside a run are unioned (commutative, so order cannot matter) and runs are
  then applied in order, preserving the only ordering the spec gives meaning to.
* Inside a run, records that share a **stroke definition** are batched into one
  Clipper call.  Two traces of the same width sweep independently, and their
  union does not depend on the order they were batched in.

The second rule is what makes the very wide layers tractable: a board with 20
distinct trace widths needs 20 Clipper calls per polarity run rather than
471,158.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Union

from . import clipper
from .clipper import Region, region_from_geometry
from .diagnostics import (
    GEOMETRY_ARC_DEGENERATE,
    GEOMETRY_EMPTY_RESULT,
    GEOMETRY_RESIZE_UNVERIFIED,
    GEOMETRY_SWEEP_ASYMMETRIC,
    GEOMETRY_SYMBOL_UNDEFINED,
    GEOMETRY_SYMBOL_UNSUPPORTED,
    GEOMETRY_ZERO_WIDTH_STROKE,
    Diagnostics,
)
from .errors import SymbolCycleError, UnsupportedFeatureError
from .features import (
    ArcFeature,
    Feature,
    FeatureFile,
    LineFeature,
    PadFeature,
    SurfaceFeature,
)
from .geometry import (
    TessellationOptions,
    arc_points,
    standard_symbol_geometry,
    transform_points,
)
from .model import LayerType, Orientation, Polarity, Point, SurfaceGeometry
from .stackup import Stackup
from .symbols import (
    StandardSymbol,
    SymbolDefinition,
    SymbolLibrary,
    parse_standard_symbol,
)

__all__ = [
    "Stroke",
    "ExpandOptions",
    "LayerGeometry",
    "Expander",
    "stroke_of",
]


# ---------------------------------------------------------------------------
# stroke definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stroke:
    """How a symbol sweeps along a line or arc.

    ``kind`` is one of:

    ``round``
        A circle of radius ``size_mm``.  Swept exactly with Clipper's rounded
        offset, which keeps the band width exact instead of inheriting the error
        of tessellating the circle first.
    ``square``
        A square of side ``size_mm``, kept axis-aligned throughout the sweep
        (spec p.38: the symbol is dragged, not rotated).  Swept exactly with a
        Minkowski sum.
    ``custom``
        A user-defined symbol.  No closed form exists, so the sweep is
        generalised from the islands and holes and reported.
    """

    kind: str
    size_mm: float = 0.0
    name: str = ""

    @property
    def key(self) -> tuple:
        """Hashable identity, quantised so float noise cannot split a batch."""
        return (self.kind, round(self.size_mm, 9), self.name)


def stroke_of(target: Union[StandardSymbol, SymbolDefinition, None]) -> Optional[Stroke]:
    """The stroke descriptor for a resolved symbol, or ``None`` if unusable."""
    if isinstance(target, StandardSymbol):
        if target.prefix == "r":
            return Stroke("round", target.size_mm(target.numbers[0]) / 2.0)
        if target.prefix == "s":
            return Stroke("square", target.size_mm(target.numbers[0]))
        return None
    if isinstance(target, SymbolDefinition):
        return Stroke("custom", 0.0, target.name)
    return None


# ---------------------------------------------------------------------------
# options and results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpandOptions:
    """Knobs for the expansion stage."""

    tessellation: TessellationOptions = field(default_factory=TessellationOptions)
    #: Maximum recursion depth for user-defined symbols that reference others.
    max_symbol_depth: int = 16
    #: Report a sweep drawn with a symbol that is neither round nor square.
    warn_unsupported_strokes: bool = True
    #: Draw a zero-width stroke (``r0``) as a hairline of this width.
    #:
    #: Zero-width artwork is real: ``designodb-rigidflex`` draws its board
    #: outline with 235 ``r0`` lines.  Mathematically they enclose no area, so
    #: the default of 0 keeps the geometry exact and therefore invisible, and a
    #: caller that wants to *see* the outline raises this.  The substitution is
    #: reported rather than silent, because it is an invention.
    minimum_stroke_mm: float = 0.0


@dataclass
class LayerGeometry:
    """The composed solid area of one layer."""

    layer_name: str
    region: Region
    #: Number of feature records considered.
    records: int = 0
    #: Number of records that produced no area (unresolved symbol, etc.).
    skipped: int = 0
    #: Polarity runs applied, i.e. boolean operations that were not batched away.
    polarity_runs: int = 0
    #: Whether the composition started from a filled base (profile) area.
    used_base: bool = False
    #: Drill layers whose projection was subtracted from this layer's base, in
    #: matrix order.  Empty for every layer that is not a dielectric sheet.
    drills_subtracted: list[str] = field(default_factory=list)
    #: Area the drill projection actually removed (mm^2).  Overlap measured
    #: before the subtraction, so a hole that was already a feature-reported void
    #: contributes less than its full area.
    drill_cut_mm2: float = 0.0

    @property
    def area_mm2(self) -> float:
        return self.region.area

    @property
    def bounds(self):
        return self.region.bounds

    @property
    def polygon_count(self) -> int:
        return len(self.region)

    def to_surface_geometry(self) -> SurfaceGeometry:
        return self.region.to_surface_geometry()


# ---------------------------------------------------------------------------
# the expander
# ---------------------------------------------------------------------------

class Expander:
    """Expands symbol references and feature records into regions.

    One instance is reused across a whole job: it caches symbol geometry (the
    same ``rect30x20`` pad appears tens of thousands of times) and deduplicates
    warnings, so a systematic problem produces one line of output rather than
    one per record.
    """

    def __init__(
        self,
        library: Optional[SymbolLibrary],
        *,
        options: Optional[ExpandOptions] = None,
        diagnostics: Optional[Diagnostics] = None,
    ) -> None:
        self.library = library
        self.options = options or ExpandOptions()
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics()
        self._geometry_cache: dict[tuple, Optional[SurfaceGeometry]] = {}
        self._definition_cache: dict[str, Optional[Region]] = {}
        self._stackups: dict[str, Stackup] = {}
        self._drill_regions: dict[tuple[str, str], Optional[Region]] = {}
        self._warned: set[str] = set()
        # Only live during a polarity run; public symbol geometries are mutable.
        self._pad_regions: Optional[dict[int, tuple[SurfaceGeometry, Region]]] = None

    # ------------------------------------------------------------- stackup

    def stackup(self, step) -> Stackup:
        """The step's layer stack, built once per step.

        Building it is cheap but not free (it walks every board layer), and the
        expander is reused across a whole job, so it is cached by step name.
        """
        cached = self._stackups.get(step.name)
        if cached is None:
            cached = Stackup(step, diagnostics=self.diagnostics)
            self._stackups[step.name] = cached
        return cached

    def drill_region(self, step, layer) -> Optional[Region]:
        """Composed projection of one drill layer, cached by step and layer.

        A dielectric's base needs the union of every drill that crosses it, and
        several dielectrics share the same drill layers -- expanding each drill
        once turns an O(dielectrics x drills) composition into O(drills).
        """
        key = (step.name, layer.name.casefold())
        if key in self._drill_regions:
            return self._drill_regions[key]

        region: Optional[Region] = None
        if layer.features is not None and layer.features.records:
            geometry = expand_layer(layer, step, expander=self, options=self.options)
            region = geometry.region if geometry.region else None
        self._drill_regions[key] = region
        return region

    # ------------------------------------------------------------- warnings

    def _warn_once(self, key: str, code: str, message: str,
                  path: Optional[str] = None) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self.diagnostics.warn(code, message, path)

    # --------------------------------------------------- symbol -> geometry

    def symbol_geometry(
        self,
        target: Union[StandardSymbol, SymbolDefinition, None],
        *,
        depth: int = 0,
    ) -> Optional[SurfaceGeometry]:
        """Millimetre geometry of a resolved symbol, centred on its origin."""
        if target is None:
            return None

        cache_key = self._cache_key(target)
        if cache_key is not None and cache_key in self._geometry_cache:
            return self._geometry_cache[cache_key]

        geometry: Optional[SurfaceGeometry]
        if isinstance(target, StandardSymbol):
            geometry = self._standard_geometry(target)
        else:
            geometry = self._definition_geometry(target, depth=depth)

        if cache_key is not None:
            self._geometry_cache[cache_key] = geometry
        return geometry

    @staticmethod
    def _cache_key(target) -> Optional[tuple]:
        if isinstance(target, StandardSymbol):
            return ("std", target.name, target.unit.value)
        if isinstance(target, SymbolDefinition):
            return ("def", target.name.casefold())
        return None

    def _standard_geometry(self, symbol: StandardSymbol) -> Optional[SurfaceGeometry]:
        try:
            return standard_symbol_geometry(symbol, self.options.tessellation)
        except UnsupportedFeatureError as exc:
            self._warn_once(
                f"unsupported:{symbol.prefix}",
                GEOMETRY_SYMBOL_UNSUPPORTED,
                f"standard symbol {symbol.name!r}: {exc}",
            )
            return None

    def _definition_geometry(self, definition: SymbolDefinition,
                             *, depth: int) -> Optional[SurfaceGeometry]:
        """Expand a user-defined symbol into geometry at its own origin."""
        key = definition.name.casefold()
        if key in self._definition_cache:
            cached = self._definition_cache[key]
            return None if cached is None else cached.to_surface_geometry()

        if depth > self.options.max_symbol_depth:
            self._warn_once(
                f"depth:{key}",
                GEOMETRY_SYMBOL_UNDEFINED,
                f"user-defined symbol {definition.name!r} exceeds the maximum "
                f"nesting depth of {self.options.max_symbol_depth}",
            )
            self._definition_cache[key] = None
            return None

        try:
            region = self._compose_file(definition.features, depth=depth + 1)
        except SymbolCycleError as exc:
            self._warn_once(
                f"cycle:{key}",
                GEOMETRY_SYMBOL_UNDEFINED,
                f"user-defined symbol {definition.name!r} contains a reference "
                f"cycle: {exc}",
            )
            region = None

        self._definition_cache[key] = region
        if region is None:
            return None
        return region.to_surface_geometry()

    # ------------------------------------------------------ record -> region

    def record_region(self, record: Feature, features: FeatureFile,
                      *, depth: int = 0) -> Optional[Region]:
        """Solid area contributed by one record, in step coordinates."""
        unit = features.units.system

        if isinstance(record, SurfaceFeature):
            return region_from_geometry(record.geometry, self.options.tessellation)

        if isinstance(record, PadFeature):
            return self._pad_region(record, unit, depth=depth)

        if isinstance(record, (LineFeature, ArcFeature)):
            return self._stroke_region(record, unit, depth=depth)

        return None

    # --------------------------------------------------------------- pads

    def _pad_region(self, record: PadFeature, unit, *, depth: int) -> Optional[Region]:
        target = self._resolve(record.symbol, unit)
        geometry = self.symbol_geometry(target, depth=depth)
        if geometry is None:
            self._missing_symbol_warning(record.symbol)
            return None

        origin = (record.x_mm, record.y_mm)

        if record.resize_mm:
            # Unverified against real data (see PadFeature.resize_mm).  Treat the
            # value as a total width increase, so each boundary moves half of it.
            if "resize" not in self._warned:
                self._warned.add("resize")
                self.diagnostics.warn(
                    GEOMETRY_RESIZE_UNVERIFIED,
                    f"apt_def resize factor is applied as a dimensional delta of "
                    f"{record.resize_mm:g} mm (spec p.170 prose and its own example "
                    f"disagree by 1000x, and no case in cases/ exercises it)",
                )
        cache = self._pad_regions
        entry = cache.get(id(geometry)) if cache is not None else None
        if entry is None:
            region = region_from_geometry(geometry, self.options.tessellation)
            if cache is not None:
                cache[id(geometry)] = (geometry, region)
        else:
            region = entry[1]
        if record.resize_mm:
            region = self._offset(region, record.resize_mm / 2.0)

        placed = self._place(region, origin, record.orientation)
        # _place's identity fast path must not expose the run's cached list.
        return Region(list(placed.rings)) if cache is not None and placed is region else placed

    # ------------------------------------------------------------ sweeps

    def _stroke_region(self, record, unit, *, depth: int) -> Optional[Region]:
        target = self._resolve(record.symbol, unit)
        stroke = stroke_of(target)
        if stroke is None:
            return self._custom_sweep(record, target, unit, depth=depth)

        path = self._path_points(record)
        if len(path) < 2:
            return None

        if stroke.kind == "round":
            return clipper.stroke_path([path], self._sweep_radius(stroke),
                                       self.options.tessellation)
        return clipper.sweep_paths(
            [_square_ring(stroke.size_mm)], [path], self.options.tessellation
        )

    def _custom_sweep(self, record, target, unit, *, depth: int) -> Optional[Region]:
        """Sweep a symbol that is neither round nor square.

        Spec p.38 permits only round and square symbols on lines and arcs --
        *"using non-symmetric symbols for the drawing of lines is not
        recommended"* -- but it also requires an application that encounters one
        to produce something sensible: *"The result should resemble the sweep of
        the symbol from its start point to its end point."*  The generalisation
        used here is the natural one: sweep the islands, subtract the swept
        holes.
        """
        geometry = self.symbol_geometry(target, depth=depth)
        if geometry is None:
            self._missing_symbol_warning(record.symbol)
            return None

        path = self._path_points(record)
        if len(path) < 2:
            return None

        if self.options.warn_unsupported_strokes:
            name = target.name if target is not None else "<unresolved>"
            symmetric = isinstance(target, StandardSymbol) and target.is_symmetric()
            if not symmetric:
                self._warn_once(
                    f"sweep:{name}",
                    GEOMETRY_SWEEP_ASYMMETRIC,
                    f"line/arc drawn with non-round symbol {name!r}; swept by "
                    f"generalising islands and holes, which spec p.38 leaves to "
                    f"the application",
                )

        return clipper.sweep(geometry, path, self.options.tessellation)

    def _path_points(self, record) -> list[Point]:
        if isinstance(record, LineFeature):
            return [record.start_mm, record.end_mm]

        arc: ArcFeature = record
        # A centre that coincides with an endpoint leaves no radius, so there is
        # no curve to sweep.
        if arc.radius_mm <= 0.0:
            self._warn_once(
                "arc:degenerate",
                GEOMETRY_ARC_DEGENERATE,
                f"arc at {arc.start_mm} with a coincident centre has no radius",
            )
            return []

        # ``arc_points`` already expands start == end into a full 360 degree
        # turn (spec p.40), so the closed centreline falls out of the ordinary
        # path.  Treating it as the degenerate two-point segment it literally is
        # used to drop the whole record: the two coincident points survived
        # deduplication as one, the path test below rejected it, and a legal
        # full-circle ring vanished with no diagnostic.
        points = [arc.start_mm]
        points.extend(arc_points(arc.start_mm, arc.end_mm, arc.center_mm,
                                 arc.clockwise, self.options.tessellation))
        return points

    # ------------------------------------------------------- composition

    def _compose_file(self, features: FeatureFile, *, depth: int = 0) -> Optional[Region]:
        """Compose every record of a feature file in spec order."""
        return self._apply_records(features.records, features, depth=depth)

    def _apply_records(self, records: Sequence[Feature], features: FeatureFile, *,
                       depth: int) -> Optional[Region]:
        """Union positive runs and subtract negative ones, in file order."""
        region = Region()
        produced_any = False

        for positive, run in _polarity_runs(records):
            run_region = self.run_region(run, features, depth=depth)
            if not run_region:
                continue
            produced_any = True
            region = clipper.union(region, run_region) if positive \
                else clipper.difference(region, run_region)

        return region if produced_any else (region if region else None)

    def run_region(self, records: Sequence[Feature], features: FeatureFile, *,
                   depth: int) -> Region:
        """Union every record of one polarity run, batching by stroke.

        Batching is safe because records inside a run all have the same
        polarity, so the union is commutative and the batch order is
        unobservable.
        """
        previous = self._pad_regions
        self._pad_regions = {}
        try:
            return self._run_region(records, features, depth=depth)
        finally:
            self._pad_regions = previous

    def _run_region(self, records: Sequence[Feature], features: FeatureFile, *,
                    depth: int) -> Region:
        parts: list[Region] = []
        strokes: dict[tuple, _StrokeBatch] = {}

        for record in records:
            if isinstance(record, (LineFeature, ArcFeature)):
                batch = self._batch_for(record, features, depth=depth)
                if batch is None:
                    continue
                path = self._path_points(record)
                if len(path) < 2:
                    continue
                strokes.setdefault(batch.key, batch).paths.append(path)
                continue

            part = self.record_region(record, features, depth=depth)
            if part:
                parts.append(part)

        for batch in strokes.values():
            batched = self._batched_stroke(batch)
            if batched:
                parts.append(batched)

        if not parts:
            return Region()
        return clipper.union(*parts)

    def _batch_for(self, record, features: FeatureFile, *,
                   depth: int) -> Optional["_StrokeBatch"]:
        """Batch descriptor for a line/arc record; ``None`` when unusable."""
        unit = features.units.system
        target = self._resolve(record.symbol, unit)
        if target is None:
            self._missing_symbol_warning(record.symbol)
            return None

        stroke = stroke_of(target)
        if stroke is None:
            # Neither round nor square: no closed form, so key the batch by the
            # symbol name and resolve its geometry lazily per path.
            stroke = Stroke("custom", 0.0, getattr(target, "name", ""))
        return _StrokeBatch(stroke=stroke, unit=unit)

    def _sweep_radius(self, stroke: Stroke) -> float:
        """Effective radius for a round stroke, honouring the hairline option."""
        if stroke.size_mm > 0.0:
            return stroke.size_mm
        if self.options.minimum_stroke_mm <= 0.0:
            return 0.0
        self._warn_once(
            "zero-width-stroke",
            GEOMETRY_ZERO_WIDTH_STROKE,
            f"zero-width (r0) line artwork is being drawn at "
            f"{self.options.minimum_stroke_mm:g} mm instead of 0; pass "
            f"--minimum-stroke 0 to keep the geometry exact",
        )
        return self.options.minimum_stroke_mm / 2.0

    def _batched_stroke(self, batch: "_StrokeBatch") -> Region:
        stroke, paths = batch.stroke, batch.paths
        if not paths:
            return Region()
        if stroke.kind == "round":
            return clipper.stroke_paths(paths, self._sweep_radius(stroke),
                                        self.options.tessellation)
        if stroke.kind == "square":
            return clipper.sweep_paths(
                [_square_ring(stroke.size_mm)], paths, self.options.tessellation
            )

        # Custom symbols: no shared descriptor, so sweep each path with its own
        # geometry.  Rare enough that per-path cost is acceptable.
        geometry = None
        if self.library is not None and stroke.name:
            geometry = self.symbol_geometry(
                self.library.resolve_name(stroke.name, batch.unit), depth=0
            )
        if geometry is None:
            return Region()

        if self.options.warn_unsupported_strokes:
            self._warn_once(
                f"sweep:{stroke.name}",
                GEOMETRY_SWEEP_ASYMMETRIC,
                f"line/arc drawn with non-round symbol {stroke.name!r}; swept by "
                f"generalising islands and holes, which spec p.38 leaves to the "
                f"application",
            )

        out = Region()
        for path in paths:
            out = clipper.union(out, clipper.sweep(geometry, path,
                                                   self.options.tessellation))
        return out

    # ---------------------------------------------------------- primitives

    def _offset(self, region: Region, delta_mm: float) -> Region:
        if not region or delta_mm == 0.0:
            return region
        return clipper.offset(region, delta_mm, self.options.tessellation)

    @staticmethod
    def _place(region: Region, origin: Point, orientation: Orientation) -> Region:
        """Rotate, then mirror, then translate a symbol's geometry (spec p.34-35).

        Rotation is clockwise and mirroring is about the x axis, and the spec
        fixes their order: *"When pads are both rotated and mirrored, they are
        rotated first, and then mirrored along the x-axis."*

        Winding is re-derived only when a mirror may have reversed it.  Rotation
        and translation have determinant +1, so leaving the winding alone in
        those cases avoids a pointless orientation test per ring -- and, more
        importantly, keeps the ``is_hole`` flags consistent with the geometry
        rather than with a recomputed area sign.
        """
        if not region:
            return region

        angle = orientation.rotation_deg
        mirrored = orientation.mirrored
        moved = origin != (0.0, 0.0)
        if angle == 0.0 and not mirrored and not moved:
            return region

        from .geometry import is_clockwise

        translated = Region()
        for ring in region.rings:
            points = transform_points(ring.points, angle=angle, mirrored=mirrored)
            if moved:
                points = [(x + origin[0], y + origin[1]) for x, y in points]
            if mirrored:
                # Mirroring negates the signed area, so the spec winding
                # (islands clockwise, holes counter-clockwise, p.39) has to be
                # restored from the hole flag rather than inherited.
                if is_clockwise(points) != (not ring.is_hole):
                    points = list(reversed(points))
            translated.rings.append(clipper.Ring(tuple(points), ring.is_hole))
        return translated

    def _resolve(self, ref, unit):
        """Resolve a symbol reference, or ``None`` if it cannot be.

        Without a library, standard names still resolve: they are pure arithmetic
        on the name (Appendix A) and need neither the archive nor the attribute
        definitions.  Only a ``symbols/`` lookup requires the library, so a job
        loaded with ``load_features=False`` -- or built by hand -- can still
        render everything drawn with standard symbols, which on real boards is
        nearly all of it.
        """
        if ref is None:
            return None

        effective = ref.effective_unit(unit)
        if self.library is None:
            return parse_standard_symbol(ref.name, effective)

        try:
            return self.library.resolve(ref, unit)
        except SymbolCycleError:
            self._warn_once(
                f"cycle:{ref.name}",
                GEOMETRY_SYMBOL_UNDEFINED,
                f"symbol {ref.name!r} takes part in a reference cycle",
            )
            return None

    def _missing_symbol_warning(self, ref) -> None:
        name = getattr(ref, "name", None) or "<unnamed>"
        self._warn_once(
            f"missing:{name}",
            GEOMETRY_SYMBOL_UNDEFINED,
            f"no geometry for symbol {name!r}; records using it contribute "
            f"nothing to the layer",
        )


@dataclass
class _StrokeBatch:
    """Centrelines sharing one stroke definition, swept in a single call."""

    stroke: Stroke
    unit: object
    paths: list[list[Point]] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return self.stroke.key + (self.unit.value,)



def _polarity_runs(records: Sequence[Feature]) -> Iterable[tuple[bool, list[Feature]]]:
    """Split records into maximal runs of equal polarity."""
    run: list[Feature] = []
    current: Optional[bool] = None
    for record in records:
        positive = record.polarity is Polarity.POSITIVE
        if current is None or positive == current:
            current = positive
            run.append(record)
            continue
        yield current, run
        current = positive
        run = [record]
    if run:
        yield bool(current), run


def _square_ring(side_mm: float) -> list[Point]:
    if side_mm <= 0.0:
        return []
    half = side_mm / 2.0
    return [(-half, -half), (half, -half), (half, half), (-half, half)]


def expand_layer(
    layer,
    step,
    *,
    expander: Expander,
    options: Optional[ExpandOptions] = None,
) -> LayerGeometry:
    """Compose one layer into a fully positive region (AGENTS constraint 2).

    The starting point depends on what the layer *is*, which is a distinction
    worth spelling out because getting it wrong is dramatic rather than subtle:

    ``DIELECTRIC``
        Starts from the base polygon of AGENTS constraint 3 -- the layer's own
        ``profile``, else the step profile.  The constraint is worded for
        dielectric layers specifically ("the dielectric layer's base polygon
        inherits the board outline"), and it is right: a dielectric layer
        physically exists wherever the board does.

    copper (``SIGNAL``/``POWER_GROUND``/``MIXED``)
        Starts empty.  Its features *are* the copper; seeding it with the board
        outline would flood the whole panel with metal.

    anything else (``SOLDER_MASK``, ``SOLDER_PASTE``, ``SILK_SCREEN``,
    ``DOCUMENT``, ``COMPONENT``, ...)
        Starts empty too.  These layers are *patterns*, not sheets -- a
        silkscreen layer is the ink, so filling it with the board outline would
        draw the whole board in white and hide the very thing the preview exists
        to show.  (An earlier version of this function did exactly that, which is
        why the distinction is now documented: every non-copper layer reported a
        suspiciously uniform ~20,477 mm^2, i.e. the board area, instead of its
        own artwork.)

    On top of that, a **negative** layer starts filled regardless of kind and
    subtracts its features, because its features describe clearance rather than
    material (spec p.39).
    """
    opts = options or expander.options
    features = layer.features
    invert = layer.is_negative

    base = Region()
    used_base = False
    if invert or _starts_from_profile(layer):
        geometry = step.base_geometry(layer)   # raises LayerProfileError
        base = region_from_geometry(geometry, opts.tessellation)
        used_base = bool(base)

    result = LayerGeometry(layer_name=layer.name, region=base, used_base=used_base)

    records = features.records if features is not None else ()
    if records:
        result.records = len(records)
        result.polarity_runs = _count_runs(records)

        # One running accumulator, seeded with the base area, then every run applied
        # in file order.  Seeding the accumulator rather than subtracting a
        # separately-accumulated region is not just tidier -- it is the difference
        # between working and not on a negative layer.  A negative layer's
        # *positive* records are voids, so subtracting them from an empty
        # accumulator yields nothing, and a plane layer would come out blank.
        region = base
        for positive, run in _polarity_runs(records):
            run_region = expander.run_region(run, features, depth=0)
            if not run_region:
                continue
            # Layer polarity inverts the meaning of every record (spec p.39): on a
            # negative layer the drawn shapes are the voids.
            if positive != invert:
                region = clipper.union(region, run_region)
            else:
                region = clipper.difference(region, run_region)
        result.region = region

    # AGENTS constraint 3, Z half: a dielectric is the board outline *minus* the
    # projection of every drill whose Z interval overlaps this dielectric's own.
    # Applied last, so a hole wins over anything the layer's own artwork drew
    # inside it, and applied even when the features file is empty -- which on a
    # real board is the common case and was exactly when the holes used to be
    # filled in.
    if result.used_base and layer.type is LayerType.DIELECTRIC:
        region, drilled, removed = _subtract_drills(
            result.region, layer, step, expander
        )
        result.region = region
        result.drills_subtracted = drilled
        result.drill_cut_mm2 = removed

    if not result.region:
        expander.diagnostics.warn(
            GEOMETRY_EMPTY_RESULT,
            f"layer {layer.name!r} composed to an empty region",
            layer.dir_name or None,
        )
    return result


def _subtract_drills(
    region: Region,
    layer,
    step,
    expander: Expander,
) -> tuple[Region, list[str], float]:
    """Remove the projection of every drill crossing *layer* (AGENTS constraint 3).

    Returns ``(region_without_holes, drill_names, removed_area_mm2)``.  Nothing
    is guessed: a span that cannot be resolved yields no subtraction and a
    diagnostic from :class:`~odbpp.stackup.Stackup`, because treating an unknown
    span as a through-hole would silently perforate the whole board.
    """
    if not region:
        return region, [], 0.0

    stackup = expander.stackup(step)
    crossing = stackup.drills_through(layer)
    if not crossing:
        return region, [], 0.0

    projection = Region()
    names: list[str] = []
    for drill in crossing:
        drill_region = expander.drill_region(step, drill)
        if not drill_region:
            continue
        projection = clipper.union(projection, drill_region)
        names.append(drill.name)

    if not projection:
        return region, [], 0.0

    removed = clipper.intersection(region, projection).area
    return clipper.difference(region, projection), names, removed


def _starts_from_profile(layer) -> bool:
    """Whether a layer is a *sheet* of material rather than a *pattern*."""
    from .model import LayerType

    return layer.type is LayerType.DIELECTRIC


def _count_runs(records: Sequence[Feature]) -> int:
    return sum(1 for _ in _polarity_runs(records))
