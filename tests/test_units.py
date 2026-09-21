"""Unit semantics tests.

This is the single most dangerous area of ODB++: coordinates use ``mm``/``inch``
while symbol parameters, resize factors and drill sizes use ``µm``/``mil``
(spec p.36, p.166).  Conflating them is a silent 1000x error, so the API
deliberately has **no** generic ``to_mm`` -- only ``coord_mm`` and ``size_mm``.
These tests pin that down.
"""

from __future__ import annotations

import pytest

from odbpp.errors import ParseError
from odbpp.units import (
    DEFAULT_UNIT_SYSTEM,
    CopperWeightKind,
    UnitScope,
    UnitSystem,
    copper_weight,
)


class TestUnitSystem:
    def test_parse_accepts_the_four_spellings(self):
        assert UnitSystem.parse("MM") is UnitSystem.MM
        assert UnitSystem.parse("mm") is UnitSystem.MM
        assert UnitSystem.parse("INCH") is UnitSystem.INCH
        assert UnitSystem.parse("inch") is UnitSystem.INCH

    def test_parse_ignores_surrounding_whitespace(self):
        assert UnitSystem.parse("  INCH \n") is UnitSystem.INCH

    def test_parse_accepts_the_historical_spellings_too(self):
        # Real exporters emit METRIC/IMPERIAL/MIL/INCHES as well as MM/INCH.
        assert UnitSystem.parse("METRIC") is UnitSystem.MM
        assert UnitSystem.parse("IMPERIAL") is UnitSystem.INCH
        assert UnitSystem.parse("MIL") is UnitSystem.INCH
        assert UnitSystem.parse("INCHES") is UnitSystem.INCH

    def test_parse_rejects_anything_else(self):
        for bad in ("MICRON", "UM", "µm", ""):
            with pytest.raises(ParseError):
                UnitSystem.parse(bad)

    def test_default_is_imperial(self):
        # Spec p.23: a product model with no UNITS is imperial, not metric.
        assert DEFAULT_UNIT_SYSTEM is UnitSystem.INCH


class TestCoordinateVersusSize:
    """A coordinate and a size with the same number mean different lengths."""

    def test_metric_coordinate_is_millimetres(self):
        scope = UnitScope(UnitSystem.MM)
        assert scope.coord_mm(1.0) == pytest.approx(1.0)

    def test_metric_size_is_micrometres(self):
        scope = UnitScope(UnitSystem.MM)
        assert scope.size_mm(1.0) == pytest.approx(0.001)

    def test_imperial_coordinate_is_inches(self):
        scope = UnitScope(UnitSystem.INCH)
        assert scope.coord_mm(1.0) == pytest.approx(25.4)

    def test_imperial_size_is_mils(self):
        scope = UnitScope(UnitSystem.INCH)
        assert scope.size_mm(1.0) == pytest.approx(0.0254)

    def test_the_same_number_differs_by_1000x_within_one_scope(self):
        for system in (UnitSystem.MM, UnitSystem.INCH):
            scope = UnitScope(system)
            assert scope.coord_mm(1.0) == pytest.approx(scope.size_mm(1.0) * 1000.0)

    def test_there_is_no_generic_converter(self):
        # Guard against a well-meaning refactor reintroducing `to_mm`.
        scope = UnitScope(UnitSystem.MM)
        assert not hasattr(scope, "to_mm")
        assert not hasattr(UnitSystem, "to_mm")

    @pytest.mark.parametrize(
        "name,unit,expected_mm",
        [
            # Appendix A parameters are in µm (metric) / mil (imperial).
            ("r300.0", UnitSystem.MM, 0.300),
            # 10.827 mil = 0.2750058 mm: an inch-mode export of a 0.275 mm drill.
            ("r10.827", UnitSystem.INCH, 0.2750058),
        ],
    )
    def test_round_symbol_diameter_from_the_spec(self, name, unit, expected_mm):
        from odbpp.symbols import parse_standard_symbol

        symbol = parse_standard_symbol(name, unit)
        assert symbol is not None
        assert symbol.size_mm(symbol.param("d")) == pytest.approx(expected_mm, abs=1e-9)

    def test_donut_from_a_real_inch_sample(self):
        # cases/designodb-rigidflex is INCH: donut_r78.74x27.559 is a 2.000 mm /
        # 0.700 mm annular ring that has been through an inch round-trip.
        from odbpp.symbols import parse_standard_symbol

        symbol = parse_standard_symbol("donut_r78.74x27.559", UnitSystem.INCH)
        assert symbol is not None
        assert symbol.size_mm(symbol.param("od")) == pytest.approx(2.000, abs=1e-5)
        assert symbol.size_mm(symbol.param("id")) == pytest.approx(0.700, abs=1e-5)


class TestCopperWeight:
    """Spec p.24: the quantity depends on the unit system."""

    def test_metric_means_thickness_in_micrometres(self):
        weight = copper_weight(35.0, UnitSystem.MM)
        assert weight.kind is CopperWeightKind.THICKNESS_UM
        assert weight.raw == 35.0
        assert weight.thickness_mm == pytest.approx(0.035)
        assert weight.ounces_per_square_foot is None

    def test_imperial_means_ounces_per_square_foot(self):
        weight = copper_weight(1.0, UnitSystem.INCH)
        assert weight.kind is CopperWeightKind.AREAL_DENSITY_OZ_FT2
        assert weight.ounces_per_square_foot == 1.0
        assert weight.thickness_mm is None

    def test_the_two_are_not_interconvertible(self):
        # There is deliberately no conversion: oz/ft^2 -> thickness needs the
        # density of the deposited copper, which the format does not carry.
        metric = copper_weight(35.0, UnitSystem.MM)
        imperial = copper_weight(1.0, UnitSystem.INCH)
        assert metric.kind is not imperial.kind
        assert metric.as_dict()["unit"] == "um"
        assert imperial.as_dict()["unit"] == "oz/ft2"
        with pytest.raises(TypeError):
            float(metric)  # type: ignore[arg-type]

    def test_negative_weight_is_rejected(self):
        with pytest.raises(ValueError):
            copper_weight(-1.0, UnitSystem.MM)


class TestScopeDefaults:
    def test_default_scope_uses_the_imperial_fallback(self):
        scope = UnitScope.default("misc/info")
        assert scope.system is UnitSystem.INCH
        assert scope.origin == "misc/info"

    def test_explicit_scope_keeps_its_origin(self):
        scope = UnitScope(UnitSystem.MM, "steps/pcb/stephdr")
        assert scope.system is UnitSystem.MM
        assert scope.origin == "steps/pcb/stephdr"

    def test_with_system_keeps_the_origin_for_error_messages(self):
        scope = UnitScope(UnitSystem.MM, "features")
        switched = scope.with_system(UnitSystem.INCH)
        assert switched.system is UnitSystem.INCH
        assert switched.origin == "features"
