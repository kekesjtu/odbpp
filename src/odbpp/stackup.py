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

__all__ = ["ZSpan", "Thickness", "Stackup"]


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
        """Interpreted ``.copper_weight``; ``None`` when absent or unusable."""
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
