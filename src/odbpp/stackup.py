"""Layer stack geometry: what sits above what, and which holes pass through it.

The parser knows the matrix row order, each layer's ``TYPE`` and the drill
layers' ``START_NAME``/``END_NAME``.  It does **not** know, without a stack
model, whether a hole in ``D_3_8`` passes through ``DIELECTRIC_5``.  AGENTS
constraint 3 requires exactly that judgement: a dielectric layer's material is
the board outline minus the projection of every drill whose Z interval overlaps
the dielectric's own.

The model used here is deliberately coarse but honest:

* Copper layers define **planes**.  Planes are indexed ``0..n-1`` from the top of
  the matrix down.
* A **dielectric** between the copper at index ``i`` and the copper at index
  ``j`` occupies the half-open interval ``(i, j)``.  The interval is derived from
  ``CU_TOP``/``CU_BOTTOM`` when those UIDs resolve (spec p.66-67), and otherwise
  from the neighbouring copper layers in matrix row order -- which is the only
  ordering the matrix guarantees, and the one research 03 says to use.
* A **drill** from ``START_NAME`` to ``END_NAME`` occupies ``(a, b)`` where
  ``a``/``b`` are the copper indices those names resolve to.

Half-open intervals are what make the overlap test the ordinary one: a drill
that ends at plane ``b`` opens no hole in the dielectric *below* ``b``.  A span
that cannot be resolved is reported and never guessed -- inferring "through the
whole board" from a missing field would silently punch 9 unnecessary holes in a
43-layer rigid-flex board.

Thickness comes from ``.layer_dielectric``, interpreted with the unit scope of
the file that declared it.  That scope is the part the old export dropped: the
value ``0.00255906`` is 65 um in an ``INCH`` attrlist and 2.6 nm in an ``MM`` one.

**Absolute Z.**  Copper-plane indices order the stack but do not give it a
length.  :meth:`Stackup.z_extent` adds millimetres on top of that ordering by
walking the board layers top-down and accumulating each layer's thickness:
``.copper_weight`` for copper (an areal density in an imperial job, converted
with an assumed copper density), ``.layer_dielectric`` for dielectric and
solder mask.  A structural layer whose thickness the format does not state
marks every Z below it ``thickness_known=False``, so the reader can tell a
measured stack from a partially assumed one.  A *decorative* layer (paste,
silkscreen, ...) without a stated thickness is marked unknown for itself -- its
zero is an assumption -- while the copper positions around it stay known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .diagnostics import (
    ATTR_LAYER_DIELECTRIC_ON_COPPER,
    STACKUP_DIELECTRIC_SPAN_UNKNOWN,
    STACKUP_DRILL_SPAN_UNKNOWN,
    STACKUP_THICKNESS_UNKNOWN,
    Diagnostics,
)
from .model import Layer, LayerContext, LayerType, Step
from .units import CopperWeight, UnitScope, copper_weight

__all__ = ["ZSpan", "ZExtent", "Thickness", "Stackup"]


@dataclass(frozen=True)
class ZSpan:
    """A half-open interval in copper-plane index space.

    ``start`` is the index of the plane the interval begins at (the top of the
    interval) and ``end`` the plane it ends at.  A copper plane has no extent in
    this model, so only dielectrics and drills carry a span.
    """

    start: int
    end: int

    @property
    def is_valid(self) -> bool:
        return self.end > self.start

    def overlaps(self, other: "ZSpan") -> bool:
        return self.start < other.end and other.start < self.end

    def as_dict(self) -> list[int]:
        return [self.start, self.end]


@dataclass(frozen=True)
class ZExtent:
    """A layer's extent on the board's Z axis, in millimetres.

    ``Z`` grows *downward* from the top of the stack (the top of the first board
    layer in matrix order), which is the convention CAM tools use and the
    opposite of the model's Y axis.  The origin is the top surface, so a copper
    layer's ``start_mm`` is where its copper begins, not where its centre is.

    ``thickness_known`` is false when the layer's own thickness is not stated by
    the format or when it rests on a structural layer above whose thickness is
    not stated; the position is then a best estimate rather than a measurement,
    and saying so is the point.  A decorative layer with an assumed zero
    thickness is therefore unknown itself without making the copper planes below
    it unknown.
    """

    start_mm: float
    end_mm: float
    thickness_known: bool
    source: str

    @property
    def thickness_mm(self) -> float:
        return self.end_mm - self.start_mm

    def as_dict(self) -> dict:
        return {
            "start_mm": self.start_mm,
            "end_mm": self.end_mm,
            "thickness_mm": self.thickness_mm,
            "thickness_known": self.thickness_known,
            "source": self.source,
        }


@dataclass(frozen=True)
class Thickness:
    """A normalised layer thickness: millimetres plus where the number came from.

    ``source`` is the attribute name, ``scope`` the unit scope that was applied,
    and ``raw`` the authored string.  Keeping all four is what lets a consumer
    audit the conversion instead of trusting it -- the failure this exists to
    prevent is a millimetre label on an inch-derived number.
    """

    thickness_mm: float
    raw: float
    source: str
    scope: str
    unit_system: str
    #: True when the field is being read on a layer class the spec does not
    #: define it for (AGENTS note 5: ``.layer_dielectric`` is a non-copper field).
    spec_deviation: bool = False

    def as_dict(self) -> dict:
        out = {
            "value_mm": self.thickness_mm,
            "raw": self.raw,
            "source": self.source,
            "attribute_units": self.unit_system,
        }
        if self.spec_deviation:
            out["spec_deviation"] = True
        return out


class Stackup:
    """The ordered board stack of one step, with drill spans resolved."""

    def __init__(self, step: Step, *, diagnostics: Optional[Diagnostics] = None) -> None:
        self.step = step
        self.diagnostics = diagnostics

        # Matrix rows are the physical order.  ``step.layers`` already follows the
        # matrix, but sorting here makes the class independent of that promise.
        self.board_layers: list[Layer] = sorted(
            (l for l in step.layers if l.ref.context is LayerContext.BOARD),
            key=lambda l: l.ref.row,
        )
        self.copper_layers: list[Layer] = [l for l in self.board_layers if l.is_copper]

        self._copper_by_name: dict[str, int] = {
            l.name.casefold(): index for index, l in enumerate(self.copper_layers)
        }
        self._copper_by_uid: dict[int, int] = {
            l.ref.uid: index for index, l in enumerate(self.copper_layers)
            if l.ref.uid is not None
        }
        self._position: dict[str, int] = {
            l.name.casefold(): index for index, l in enumerate(self.board_layers)
        }
        self._warned: set[str] = set()
        #: Lazily built absolute-Z table, keyed by layer name.
        self._z_table: Optional[dict[str, ZExtent]] = None

    # ------------------------------------------------------------ primitives

    def _warn_once(self, key: str, code: str, message: str,
                   path: Optional[str] = None) -> None:
        if self.diagnostics is None or key in self._warned:
            return
        self._warned.add(key)
        self.diagnostics.warn(code, message, path)

    def copper_index(self, name: str) -> Optional[int]:
        """Index of the copper layer called *name*, or ``None``."""
        return self._copper_by_name.get(name.strip().casefold()) if name else None

    def layer_index(self, layer: Layer) -> Optional[int]:
        """Position of *layer* in matrix row order."""
        return self._position.get(layer.name.casefold())

    def _neighbouring_copper(self, layer: Layer) -> tuple[Optional[int], Optional[int]]:
        """Copper indices immediately above and below *layer* by matrix row."""
        position = self.layer_index(layer)
        if position is None:
            return None, None
        above = below = None
        for index in range(position - 1, -1, -1):
            candidate = self.board_layers[index]
            folded = candidate.name.casefold()
            if folded in self._copper_by_name:
                above = self._copper_by_name[folded]
                break
        for index in range(position + 1, len(self.board_layers)):
            candidate = self.board_layers[index]
            folded = candidate.name.casefold()
            if folded in self._copper_by_name:
                below = self._copper_by_name[folded]
                break
        return above, below

    # ---------------------------------------------------------------- spans

    def dielectric_span(self, layer: Layer) -> Optional[ZSpan]:
        """Copper interval a dielectric layer fills, or ``None`` if unknown."""
        ref = layer.ref
        if ref.cu_top is not None and ref.cu_bottom is not None:
            top = self._copper_by_uid.get(ref.cu_top)
            bottom = self._copper_by_uid.get(ref.cu_bottom)
            if top is not None and bottom is not None and bottom > top:
                return ZSpan(top, bottom)

        above, below = self._neighbouring_copper(layer)
        if above is None or below is None or below <= above:
            self._warn_once(
                f"dielectric-span:{layer.name}",
                STACKUP_DIELECTRIC_SPAN_UNKNOWN,
                f"dielectric layer {layer.name!r} is not bracketed by two copper "
                f"layers in matrix row order, so the drills that pass through it "
                f"cannot be determined; its material is left unperforated",
                layer.dir_name or None,
            )
            return None
        return ZSpan(above, below)

    def drill_span(self, layer: Layer) -> Optional[ZSpan]:
        """Copper interval a drill layer spans, or ``None`` if unknown.

        ``START_NAME``/``END_NAME`` are the only fields that state a drill's
        extent.  A missing or unresolvable endpoint is reported rather than
        widened to the whole board: the fallback would be indistinguishable from
        a genuine through-hole in the output while being far more destructive.
        """
        ref = layer.ref
        start = self.copper_index(ref.start_name)
        end = self.copper_index(ref.end_name)
        if start is None or end is None:
            self._warn_once(
                f"drill-span:{layer.name}",
                STACKUP_DRILL_SPAN_UNKNOWN,
                f"drill layer {layer.name!r} declares START_NAME="
                f"{ref.start_name!r}/END_NAME={ref.end_name!r}, which do not both "
                f"resolve to copper layers; its Z interval is unknown and it is "
                f"not projected into any dielectric",
                layer.dir_name or None,
            )
            return None
        if end < start:
            start, end = end, start
        if end == start:
            self._warn_once(
                f"drill-span:{layer.name}",
                STACKUP_DRILL_SPAN_UNKNOWN,
                f"drill layer {layer.name!r} starts and ends on the same copper "
                f"layer ({ref.start_name!r}); no dielectric lies between them",
                layer.dir_name or None,
            )
            return None
        return ZSpan(start, end)

    def span_of(self, layer: Layer) -> Optional[ZSpan]:
        """Z interval of a dielectric sheet or a drill, else ``None``.

        ``ROUT`` is excluded on purpose: it is a milling contour rather than a
        drilled hole, and AGENTS constraint 3 projects *drills* into dielectrics.
        """
        if layer.type is LayerType.DIELECTRIC:
            return self.dielectric_span(layer)
        if layer.type is LayerType.DRILL:
            return self.drill_span(layer)
        return None

    # ------------------------------------------------------------ overlap

    def drill_layers(self) -> list[Layer]:
        """Physical drill layers, in matrix order (``ROUT`` excluded)."""
        return [l for l in self.board_layers if l.type is LayerType.DRILL]

    def drills_through(self, layer: Layer) -> list[Layer]:
        """Drill layers whose Z interval overlaps *layer*'s.

        Empty when either side's span is unknown -- an unresolved span is a
        reported data problem, not a licence to guess.
        """
        span = self.span_of(layer)
        if span is None:
            return []
        out = []
        for drill in self.drill_layers():
            if drill is layer:
                continue
            other = self.drill_span(drill)
            if other is not None and span.overlaps(other):
                out.append(drill)
        return out

    # --------------------------------------------------------- thickness

    def thickness(self, layer: Layer) -> Optional[Thickness]:
        """Normalised ``.layer_dielectric`` thickness, or ``None``.

        The attribute is interpreted with the unit scope of the file that
        declared it (the layer's own ``attrlist`` when it states ``UNITS``, else
        the job default).  For a dielectric there is no thickness without that
        field, so its absence is reported.
        """
        raw = layer.attributes.get(".layer_dielectric")
        if raw is None or str(raw).strip() == "":
            if layer.type is LayerType.DIELECTRIC:
                self._warn_once(
                    f"thickness:{layer.name}",
                    STACKUP_THICKNESS_UNKNOWN,
                    f"dielectric layer {layer.name!r} declares no "
                    f".layer_dielectric, so its thickness in millimetres is unknown",
                    layer.dir_name or None,
                )
            return None

        scope = self.scope_of(layer)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self._warn_once(
                f"thickness:{layer.name}",
                STACKUP_THICKNESS_UNKNOWN,
                f"layer {layer.name!r} has a non-numeric .layer_dielectric "
                f"{raw!r}; its thickness is unknown",
                layer.dir_name or None,
            )
            return None

        deviation = layer.is_copper
        if deviation:
            self._warn_once(
                f"dielectric-on-copper:{layer.name}",
                ATTR_LAYER_DIELECTRIC_ON_COPPER,
                f"copper layer {layer.name!r} declares .layer_dielectric="
                f"{raw!r}; spec p.231 / AGENTS note 5 define that field for "
                f"non-copper layers, so the value is emitted but flagged "
                f"spec_deviation=true",
                layer.dir_name or None,
            )
        return Thickness(
            thickness_mm=scope.coord_mm(value),
            raw=value,
            source=".layer_dielectric",
            scope=scope.origin or "<job default>",
            unit_system=scope.system.value,
            spec_deviation=deviation,
        )

    def weight(self, layer: Layer) -> Optional[CopperWeight]:
        """Interpreted ``.copper_weight``; ``None`` when absent or unusable.

        An imperial value stays an areal density (:meth:`CopperWeight.kind`) but
        now also carries a thickness derived with an assumed copper density, so
        the absolute Z walk has a number to use.
        """
        raw = layer.attributes.get(".copper_weight")
        if raw is None or str(raw).strip() == "":
            return None
        scope = self.scope_of(layer)
        try:
            return copper_weight(float(raw), scope.system)
        except (TypeError, ValueError):
            self._warn_once(
                f"weight:{layer.name}",
                STACKUP_THICKNESS_UNKNOWN,
                f"layer {layer.name!r} has a non-numeric .copper_weight {raw!r}; "
                f"its copper weight is unknown",
                layer.dir_name or None,
            )
            return None

    # ------------------------------------------------------------- absolute Z

    def _material_thickness(self, layer: Layer) -> tuple[float, bool, str]:
        """Best available thickness for *layer*: ``(mm, stated, source)``.

        Copper prefers ``.copper_weight`` (its own physical quantity) and falls
        back to ``.layer_dielectric`` when a CAM tool wrote one; the non-copper
        materials use ``.layer_dielectric``.  *stated* says whether the format
        actually carried the number, which is what decides whether the stack
        position is a measurement or an assumption.
        """
        if layer.is_copper:
            weight = self.weight(layer)
            if weight is not None:
                return weight.thickness_mm, True, ".copper_weight"
            thickness = self.thickness(layer)
            if thickness is not None:
                return thickness.thickness_mm, True, thickness.source
            return 0.0, False, "unknown"

        if layer.type is not None and layer.type.is_non_copper_material:
            thickness = self.thickness(layer)
            if thickness is not None:
                return thickness.thickness_mm, True, thickness.source
            return 0.0, False, "unknown"

        return 0.0, False, "not_a_material"

    @staticmethod
    def _is_structural(layer: Layer) -> bool:
        """True for layers that physically separate or carry copper.

        Only these can make the stack's absolute Z uncertain.  Solder paste,
        silkscreen, stiffeners, component outlines and documentation are printed
        on or added to the board rather than part of its laminate, so a missing
        thickness on one of them leaves the copper plane positions intact
        instead of invalidating the whole stack.
        """
        if layer.is_copper:
            return True
        return layer.type in (LayerType.DIELECTRIC, LayerType.SOLDER_MASK)

    def _build_z_table(self) -> dict[str, ZExtent]:
        out: dict[str, ZExtent] = {}
        plane_top: dict[int, float] = {}
        plane_bottom: dict[int, float] = {}
        z = 0.0
        # Whether the cumulative position reached so far rests only on stated
        # thicknesses.  Two questions are kept apart on purpose: *where* a layer
        # sits (does anything structural above it lack a thickness?) and *how
        # thick* the layer itself is.  A decorative film with no stated thickness
        # does not move the copper planes, so it must not invalidate their Z --
        # but its own thickness is still an assumption and has to say so.
        position_known = True

        # Drills are holes, not slabs: they take their Z from the copper planes
        # they connect, so they are skipped in the cumulative walk.
        for layer in self.board_layers:
            if layer.type is LayerType.DRILL:
                continue
            value, stated, source = self._material_thickness(layer)
            start = z
            end = z + value
            z = end
            known = stated and position_known
            # Only a structural gap makes the plane positions below it uncertain.
            if not stated and self._is_structural(layer):
                position_known = False
            out[layer.name] = ZExtent(start, end, known, source)
            index = self.copper_index(layer.name)
            if layer.is_copper and index is not None:
                plane_top[index] = start
                plane_bottom[index] = end

        for layer in self.board_layers:
            if layer.type is not LayerType.DRILL:
                continue
            span = self.drill_span(layer)
            if span is None:
                continue
            if span.start not in plane_top or span.end not in plane_bottom:
                continue
            end_plane = self.copper_layers[span.end]
            reference = out.get(end_plane.name)
            out[layer.name] = ZExtent(
                plane_top[span.start],
                plane_bottom[span.end],
                bool(reference is not None and reference.thickness_known),
                f"drill span {self.copper_layers[span.start].name}"
                f"..{end_plane.name}",
            )
        return out

    def z_table(self) -> dict[str, ZExtent]:
        if self._z_table is None:
            self._z_table = self._build_z_table()
        return self._z_table

    def z_extent(self, layer: Layer) -> Optional[ZExtent]:
        """Absolute Z extent of a board layer, or ``None``.

        ``None`` for layers outside the board context (documentation,
        component outlines) and for a ``ROUT`` contour, which has no Z span in
        this model.  The result is a position in millimetres measured from the
        top of the stack; see :class:`ZExtent`.
        """
        if layer.ref.context is not LayerContext.BOARD:
            return None
        return self.z_table().get(layer.name)


    def scope_of(self, layer: Layer) -> UnitScope:
        """Unit scope that applies to a layer's ``attrlist``.

        The layer's own scope when the loader recorded one, else the step scope
        (which itself falls back to the job default).  The loader always records
        a scope, so the last resort only matters for hand-built models.
        """
        scope = getattr(layer, "attribute_scope", None)
        if scope is not None:
            return scope
        if self.step.header is not None:
            return self.step.header.units
        return UnitScope.default()


def describe_span(stackup: Stackup, span: Optional[ZSpan]) -> Optional[dict]:
    """JSON-ready description of a span, naming the copper planes at each end."""
    if span is None:
        return None
    names = [l.name for l in stackup.copper_layers]
    top = names[span.start] if 0 <= span.start < len(names) else None
    bottom = names[span.end] if 0 <= span.end < len(names) else None
    return {
        "from_layer": top,
        "to_layer": bottom,
        "copper_index": span.as_dict(),
    }
