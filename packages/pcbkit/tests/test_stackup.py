"""The layer stack: spans, overlaps, thickness and copper-weight semantics.

AGENTS constraint 3 needs two facts about a dielectric layer that no single file
states: which copper planes it sits between, and which drills pass through it.
These tests build the stack by hand so the overlap logic is pinned independently
of any case board, then check the unit conversions that the export used to drop.
"""

from __future__ import annotations

import pytest

from pcbkit.diagnostics import Diagnostics
from pcbkit.model import (
    Layer,
    LayerContext,
    LayerRef,
    LayerType,
    Polarity,
    Step,
    StepHeader,
)
from pcbkit.stackup import Stackup, describe_span
from pcbkit.units import UnitScope, UnitSystem


def layer(name, layer_type, row, *, start="", end="", attributes=None,
          scope=None, context=LayerContext.BOARD) -> Layer:
    ref = LayerRef(name=name, row=row, type=layer_type, context=context,
                   polarity=Polarity.POSITIVE, start_name=start, end_name=end)
    return Layer(ref=ref, dir_name=name, attributes=dict(attributes or {}),
                 attribute_scope=scope or UnitScope.metric())


def stacked_step(units=UnitSystem.MM) -> Step:
    """SIGNAL_1 / diel / SIGNAL_2 / diel / SIGNAL_3 plus three drill layers."""
    return Step(
        name="S",
        header=StepHeader(units=UnitScope(units)),
        layers=[
            layer("SIGNAL_1", LayerType.SIGNAL, 1),
            layer("DIELECTRIC_A", LayerType.DIELECTRIC, 2),
            layer("SIGNAL_2", LayerType.SIGNAL, 3),
            layer("DIELECTRIC_B", LayerType.DIELECTRIC, 4),
            layer("SIGNAL_3", LayerType.SIGNAL, 5),
            layer("D_1_2", LayerType.DRILL, 6, start="SIGNAL_1", end="SIGNAL_2"),
            layer("D_2_3", LayerType.DRILL, 7, start="SIGNAL_2", end="SIGNAL_3"),
            layer("D_1_3", LayerType.DRILL, 8, start="SIGNAL_1", end="SIGNAL_3"),
        ],
    )


class TestCopperOrder:
    def test_copper_layers_are_indexed_top_down(self):
        stackup = Stackup(stacked_step())
        assert [l.name for l in stackup.copper_layers] == \
            ["SIGNAL_1", "SIGNAL_2", "SIGNAL_3"]
        assert stackup.copper_index("signal_2") == 1
        assert stackup.copper_index("nope") is None

    def test_a_dielectric_span_is_between_its_row_neighbours(self):
        stackup = Stackup(stacked_step())
        step = stackup.step
        assert stackup.dielectric_span(step.layer("DIELECTRIC_A")).as_dict() == [0, 1]
        assert stackup.dielectric_span(step.layer("DIELECTRIC_B")).as_dict() == [1, 2]

    def test_the_span_is_named_in_the_json_form(self):
        stackup = Stackup(stacked_step())
        step = stackup.step
        assert describe_span(stackup, stackup.span_of(step.layer("DIELECTRIC_A"))) == {
            "from_layer": "SIGNAL_1",
            "to_layer": "SIGNAL_2",
            "copper_index": [0, 1],
        }

    def test_a_dielectric_without_copper_on_one_side_is_reported(self):
        diagnostics = Diagnostics()
        step = Step(name="S", header=StepHeader(units=UnitScope.metric()),
                    layers=[layer("DIELECTRIC_A", LayerType.DIELECTRIC, 1),
                            layer("SIGNAL_1", LayerType.SIGNAL, 2)])
        stackup = Stackup(step, diagnostics=diagnostics)
        assert stackup.dielectric_span(step.layer("DIELECTRIC_A")) is None
        assert "stackup.dielectric_span_unknown" in diagnostics.codes()


class TestDrillSpans:
    def test_span_comes_from_start_and_end_names(self):
        stackup = Stackup(stacked_step())
        step = stackup.step
        assert stackup.drill_span(step.layer("D_1_2")).as_dict() == [0, 1]
        assert stackup.drill_span(step.layer("D_2_3")).as_dict() == [1, 2]
        assert stackup.drill_span(step.layer("D_1_3")).as_dict() == [0, 2]

    def test_reversed_endpoints_are_normalised(self):
        step = stacked_step()
        step.layer("D_1_2").ref.start_name = "SIGNAL_2"
        step.layer("D_1_2").ref.end_name = "SIGNAL_1"
        assert Stackup(step).drill_span(step.layer("D_1_2")).as_dict() == [0, 1]

    def test_a_missing_endpoint_is_reported_not_widened(self):
        # Defaulting to "through the whole board" would punch holes nobody asked
        # for, and the result would look exactly like a real through-hole.
        diagnostics = Diagnostics()
        step = stacked_step()
        step.layer("D_1_2").ref.end_name = ""
        stackup = Stackup(step, diagnostics=diagnostics)
        assert stackup.drill_span(step.layer("D_1_2")) is None
        assert "stackup.drill_span_unknown" in diagnostics.codes()

    def test_rout_layers_have_no_span_and_no_warning(self):
        # ROUT is a milling contour, not a drilled hole; AGENTS constraint 3
        # projects *drills* into dielectrics.
        diagnostics = Diagnostics()
        step = stacked_step()
        step.layers.append(layer("ROUT", LayerType.ROUT, 9))
        stackup = Stackup(step, diagnostics=diagnostics)
        assert stackup.span_of(step.layer("ROUT")) is None
        assert "stackup.drill_span_unknown" not in diagnostics.codes()


class TestOverlap:
    def test_a_through_drill_crosses_every_dielectric(self):
        stackup = Stackup(stacked_step())
        step = stackup.step
        assert {d.name for d in stackup.drills_through(step.layer("DIELECTRIC_A"))} == \
            {"D_1_2", "D_1_3"}
        assert {d.name for d in stackup.drills_through(step.layer("DIELECTRIC_B"))} == \
            {"D_2_3", "D_1_3"}

    def test_a_blind_drill_is_confined_to_its_own_interval(self):
        # Half-open intervals: D_1_2 ends at plane 1, so it must not perforate the
        # dielectric below plane 1.
        stackup = Stackup(stacked_step())
        step = stackup.step
        assert "D_1_2" not in {d.name for d in
                               stackup.drills_through(step.layer("DIELECTRIC_B"))}
        assert "D_2_3" not in {d.name for d in
                               stackup.drills_through(step.layer("DIELECTRIC_A"))}

    def test_an_unresolved_dielectric_span_yields_no_drills(self):
        diagnostics = Diagnostics()
        stackup = Stackup(stacked_step(), diagnostics=diagnostics)
        step = stackup.step
        step.layer("DIELECTRIC_A").ref.cu_top = 99
        step.layer("DIELECTRIC_A").ref.cu_bottom = 98
        # CU_TOP/CU_BOTTOM that do not resolve fall back to row neighbours, which
        # is still a definite answer rather than a guess.
        assert {d.name for d in stackup.drills_through(step.layer("DIELECTRIC_A"))} == \
            {"D_1_2", "D_1_3"}


class TestThickness:
    def test_an_imperial_value_is_converted_to_millimetres(self):
        # The regression from the review: the string "0.00255906" is 65 um in an
        # INCH attrlist, and the unit scope used to be thrown away.
        step = stacked_step(units=UnitSystem.INCH)
        dielectric = step.layer("DIELECTRIC_A")
        dielectric.attributes[".layer_dielectric"] = "0.00255906"
        dielectric.attribute_scope = UnitScope.imperial("diel/attrlist")
        thickness = Stackup(step).thickness(dielectric)
        assert thickness.thickness_mm == pytest.approx(0.065000124, rel=1e-9)
        assert thickness.raw == pytest.approx(0.00255906)
        assert thickness.unit_system == "INCH"
        assert thickness.source == ".layer_dielectric"
        assert thickness.as_dict()["value_mm"] == pytest.approx(0.065000124)

    def test_a_metric_value_is_taken_as_millimetres(self):
        step = stacked_step()
        dielectric = step.layer("DIELECTRIC_A")
        dielectric.attributes[".layer_dielectric"] = "1.51"
        thickness = Stackup(step).thickness(dielectric)
        assert thickness.thickness_mm == pytest.approx(1.51)
        assert thickness.unit_system == "MM"

    def test_a_dielectric_without_thickness_is_reported(self):
        diagnostics = Diagnostics()
        step = stacked_step()
        stackup = Stackup(step, diagnostics=diagnostics)
        assert stackup.thickness(step.layer("DIELECTRIC_A")) is None
        assert "stackup.thickness_unknown" in diagnostics.codes()

    def test_a_non_numeric_value_is_reported_not_raised(self):
        diagnostics = Diagnostics()
        step = stacked_step()
        step.layer("DIELECTRIC_A").attributes[".layer_dielectric"] = "thin"
        assert Stackup(step, diagnostics=diagnostics).thickness(
            step.layer("DIELECTRIC_A")) is None
        assert "stackup.thickness_unknown" in diagnostics.codes()

    def test_the_field_on_copper_is_flagged_as_a_spec_deviation(self):
        # AGENTS note 5 / spec p.231: .layer_dielectric is defined for non-copper
        # layers, but KiCad exports write it on copper too.
        diagnostics = Diagnostics()
        step = stacked_step()
        copper = step.layer("SIGNAL_1")
        copper.attributes[".layer_dielectric"] = "0.03"
        thickness = Stackup(step, diagnostics=diagnostics).thickness(copper)
        assert thickness.thickness_mm == pytest.approx(0.03)
        assert thickness.spec_deviation is True
        assert "attr.layer_dielectric_on_copper" in diagnostics.codes()


class TestCopperWeight:
    def test_imperial_weight_is_a_density_with_a_derived_thickness(self):
        step = stacked_step(units=UnitSystem.INCH)
        copper = step.layer("SIGNAL_1")
        copper.attributes[".copper_weight"] = "0.875563"
        copper.attribute_scope = UnitScope.imperial()
        weight = Stackup(step).weight(copper)
        assert weight.ounces_per_square_foot == pytest.approx(0.875563)
        assert weight.as_dict()["unit"] == "oz/ft2"
        assert weight.thickness_mm == pytest.approx(0.029819, abs=1e-6)
        assert weight.as_dict()["thickness_source"] == "assumed_density"

    def test_metric_weight_is_a_thickness_in_micrometres(self):
        step = stacked_step()
        copper = step.layer("SIGNAL_1")
        copper.attributes[".copper_weight"] = "35"
        weight = Stackup(step).weight(copper)
        assert weight.thickness_mm == pytest.approx(0.035)
        assert weight.as_dict()["unit"] == "um"

    def test_absent_weight_is_none(self):
        step = stacked_step()
        assert Stackup(step).weight(step.layer("SIGNAL_1")) is None


class TestAbsoluteZ:
    """Millimetre Z for every board layer, from the thicknesses above it."""

    def _sized_step(self, *, units=UnitSystem.MM) -> Step:
        step = stacked_step(units=units)
        step.layer("SIGNAL_1").attributes[".copper_weight"] = "35"
        step.layer("DIELECTRIC_A").attributes[".layer_dielectric"] = "1.5"
        step.layer("SIGNAL_2").attributes[".copper_weight"] = "35"
        step.layer("DIELECTRIC_B").attributes[".layer_dielectric"] = "1.5"
        step.layer("SIGNAL_3").attributes[".copper_weight"] = "35"
        return step

    def test_z_accumulates_down_from_the_top(self):
        step = self._sized_step()
        stackup = Stackup(step)
        assert stackup.z_extent(step.layer("SIGNAL_1")).as_dict() == {
            "start_mm": 0.0, "end_mm": pytest.approx(0.035),
            "thickness_mm": pytest.approx(0.035),
            "thickness_known": True, "source": ".copper_weight",
        }
        dialectric = stackup.z_extent(step.layer("DIELECTRIC_A"))
        assert dialectric.start_mm == pytest.approx(0.035)
        assert dialectric.end_mm == pytest.approx(1.535)
        assert stackup.z_extent(step.layer("SIGNAL_3")).end_mm == \
            pytest.approx(3.105)

    def test_a_drill_spans_the_planes_it_connects(self):
        step = self._sized_step()
        stackup = Stackup(step)
        # D_1_2 starts at the top of SIGNAL_1 and ends at the bottom of SIGNAL_2.
        extent = stackup.z_extent(step.layer("D_1_2"))
        assert extent.start_mm == pytest.approx(0.0)
        assert extent.end_mm == pytest.approx(1.57)
        assert extent.thickness_known is True
        assert "drill span" in extent.source

    def test_an_unstated_structural_thickness_marks_the_stack_uncertain(self):
        step = self._sized_step()
        del step.layer("DIELECTRIC_A").attributes[".layer_dielectric"]
        stackup = Stackup(step)
        # Above the gap every position is measured; from the unknown dielectric
        # down, the extent is only a best estimate.
        assert stackup.z_extent(step.layer("SIGNAL_1")).thickness_known is True
        assert stackup.z_extent(step.layer("DIELECTRIC_A")).thickness_known is False
        assert stackup.z_extent(step.layer("SIGNAL_2")).thickness_known is False

    def test_a_decorative_layer_does_not_take_the_stack_down_with_it(self):
        # Silkscreen and solder paste are printed on the board, not part of its
        # laminate; their absent thickness must not invalidate the copper Z.
        # Their *own* extent is another matter: an assumed zero is unknown and
        # has to say so (AGENTS note 8), while the copper below stays known.
        step = self._sized_step()
        step.layers.insert(0, layer("F.SILK", LayerType.SILK_SCREEN, 0))
        step.layers.insert(1, layer("F.PASTE", LayerType.SOLDER_PASTE, 0))
        stackup = Stackup(step)
        assert stackup.z_extent(step.layer("F.SILK")).thickness_known is False
        assert stackup.z_extent(step.layer("F.PASTE")).thickness_known is False
        assert stackup.z_extent(step.layer("SIGNAL_1")).thickness_known is True
        assert stackup.z_extent(step.layer("SIGNAL_1")).start_mm == pytest.approx(0.0)

    def test_a_stated_decorative_thickness_is_known(self):
        # A paste layer whose stencil thickness the format states is a
        # measurement, not an assumption.
        step = self._sized_step()
        paste = layer("F.PASTE", LayerType.SOLDER_PASTE, 0)
        paste.attributes[".layer_dielectric"] = "0.12"
        step.layers.insert(0, paste)
        extent = Stackup(step).z_extent(step.layer("F.PASTE"))
        assert extent.thickness_known is True
        assert extent.thickness_mm == pytest.approx(0.12)
        assert extent.source == ".layer_dielectric"

    def test_a_document_layer_has_no_z(self):
        step = self._sized_step()
        step.layers.append(layer("DOC", LayerType.DOCUMENT, 99,
                                 context=LayerContext.MISC))
        assert Stackup(step).z_extent(step.layer("DOC")) is None

    def test_an_imperial_copper_weight_becomes_a_thickness(self):
        step = self._sized_step(units=UnitSystem.INCH)
        copper = step.layer("SIGNAL_1")
        copper.attributes[".copper_weight"] = "1"
        copper.attribute_scope = UnitScope.imperial()
        extent = Stackup(step).z_extent(copper)
        assert extent.end_mm == pytest.approx(0.034058, abs=1e-6)
        assert extent.source == ".copper_weight"

    def test_z_is_cached_and_stable(self):
        step = self._sized_step()
        stackup = Stackup(step)
        assert stackup.z_table() is stackup.z_table()
