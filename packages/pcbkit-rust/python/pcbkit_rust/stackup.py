"""Matrix layer order, drill endpoint references and attribute-based absolute Z.

Drill coverage compares dielectric rows with endpoint copper rows. Thickness
and cumulative absolute Z describe dimensions and never select drill coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .diagnostics import (
    ATTR_LAYER_DIELECTRIC_ON_COPPER,
    STACKUP_DRILL_ENDPOINTS_UNKNOWN,
    STACKUP_THICKNESS_UNKNOWN,
    Diagnostics,
)
from .model import Layer, LayerContext, LayerType, Step
from .units import CopperWeight, UnitScope, copper_weight

__all__ = ["ZExtent", "Thickness", "Stackup"]


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
    #: define it for (requirements.md non-copper thickness rule: ``.layer_dielectric`` is a non-copper field).
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
    """The ordered board stack of one step, with drill endpoints resolved."""

    def __init__(self, step: Step, *, diagnostics: Optional[Diagnostics] = None) -> None:
        self.step = step
        self.diagnostics = diagnostics

        # Matrix rows are the physical order.  ``step.layers`` already follows the
        # matrix, but sorting here makes the class independent of that promise.
        self.board_layers: list[Layer] = sorted(
            (l for l in step.layers if l.ref.context is LayerContext.BOARD),
            key=lambda l: l.ref.row,
        )
        self._copper_by_name = {layer.name.casefold(): layer for layer in self.board_layers if layer.is_copper}
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

    def drill_endpoints(self, layer: Layer) -> Optional[tuple[Layer, Layer]]:
        """Resolve START_NAME/END_NAME to distinct BOARD copper layers, top first."""
        ref = layer.ref
        start = self._copper_by_name.get(ref.start_name.strip().casefold())
        end = self._copper_by_name.get(ref.end_name.strip().casefold())
        if start is None or end is None or start is end:
            self._warn_once(
                f"drill-endpoints:{layer.name}", STACKUP_DRILL_ENDPOINTS_UNKNOWN,
                f"drill layer {layer.name!r}: START_NAME={ref.start_name!r}/"
                f"END_NAME={ref.end_name!r} must resolve to two distinct BOARD copper layers",
                layer.dir_name or None,
            )
            return None
        return (start, end) if start.ref.row < end.ref.row else (end, start)

    def drill_layers(self) -> list[Layer]:
        """Physical drill layers, in matrix order (``ROUT`` excluded)."""
        return [l for l in self.board_layers if l.type is LayerType.DRILL]

    def drills_through(self, layer: Layer) -> list[Layer]:
        """Drills whose endpoint copper rows strictly bracket the dielectric."""
        if layer.type is not LayerType.DIELECTRIC or layer.ref.context is not LayerContext.BOARD:
            return []
        out = []
        for drill in self.drill_layers():
            endpoints = self.drill_endpoints(drill)
            if (endpoints is not None
                    and endpoints[0].ref.row < layer.ref.row < endpoints[1].ref.row):
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
                f"{raw!r}; spec p.231 / requirements.md non-copper thickness rule define that field for "
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

        for layer in self.drill_layers():
            endpoints = self.drill_endpoints(layer)
            if endpoints is None:
                continue
            top, bottom = endpoints
            out[layer.name] = ZExtent(
                out[top.name].start_mm, out[bottom.name].end_mm,
                out[bottom.name].thickness_known,
                f"drill endpoints {top.name}..{bottom.name}",
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
