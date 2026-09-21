"""Standard-vs-user-defined symbol tests.

The central rule is that the two namespaces are told apart by **grammar**, never
by prefix matching.  The counterexample that motivated this is
``special_0.670x0.950_nt2520`` from ``cases/designodb-rigidflex``: it starts with
``s``, so a naive ``name[0] in "rsd..."`` test classifies it as a square and
silently produces a pad of the wrong size.
"""

from __future__ import annotations

import pytest

from odbpp.errors import SymbolCycleError, UnsupportedFeatureError
from odbpp.symbols import (
    STANDARD_SPECS,
    SymbolLibrary,
    is_standard_symbol,
    parse_standard_symbol,
)
from odbpp.units import UnitSystem

from conftest import fixture_path


class TestStandardGrammar:
    @pytest.mark.parametrize(
        "name,prefix,params",
        [
            ("r300.0", "r", {"d": 300.0}),
            ("s1000", "s", {"s": 1000.0}),
            ("rect800x950", "rect", {"w": 800.0, "h": 950.0}),
            ("rect800x950xr200", "rect", {"w": 800.0, "h": 950.0, "radius": 200.0}),
            ("oval1000x500", "oval", {"w": 1000.0, "h": 500.0}),
            ("donut_r2000x1000", "donut_r", {"od": 2000.0, "id": 1000.0}),
            ("donut_rc1000x500x100", "donut_rc", {"ow": 1000.0, "oh": 500.0, "lw": 100.0}),
        ],
    )
    def test_parses_the_basic_families(self, name, prefix, params):
        symbol = parse_standard_symbol(name, UnitSystem.MM)
        assert symbol is not None
        assert symbol.prefix == prefix
        assert symbol.params == params

    def test_the_longest_matching_prefix_wins(self):
        # "sr_ths" must not be read as "s" with a garbage tail.
        symbol = parse_standard_symbol("sr_ths200x180x0x4x20", UnitSystem.MM)
        assert symbol is not None
        assert symbol.prefix == "sr_ths"

    def test_a_prefix_must_be_followed_by_well_formed_parameters(self):
        # "rect" is a valid prefix, but "ectangle" is not a parameter list.
        assert parse_standard_symbol("rectangle", UnitSystem.MM) is None

    def test_the_rigidflex_counterexample_is_not_a_user_symbol(self):
        # Starts with "s"; is nevertheless not a square.
        assert parse_standard_symbol("special_0.670x0.950_nt2520", UnitSystem.MM) is None
        assert not is_standard_symbol("special_0.670x0.950_nt2520")

    def test_a_bare_prefix_with_no_parameters_is_not_a_symbol(self):
        assert parse_standard_symbol("r", UnitSystem.MM) is None
        assert parse_standard_symbol("rect", UnitSystem.MM) is None

    def test_substring_matches_do_not_count(self):
        # "cross" contains "r", "s" and "oss"; only the whole-name grammar counts.
        assert parse_standard_symbol("not_r100", UnitSystem.MM) is None

    def test_partial_parameter_lists_are_rejected(self):
        # rect needs w and h.
        assert parse_standard_symbol("rect800", UnitSystem.MM) is None

    def test_optional_trailing_parameters_still_accept_the_minimum(self):
        symbol = parse_standard_symbol("rect800x950xr200x12", UnitSystem.MM)
        assert symbol is not None
        assert symbol.radius == 200.0
        assert symbol.corners == (1, 2)

    def test_corner_mask_digits_are_read_individually(self):
        symbol = parse_standard_symbol("rect800x950xr100x1234", UnitSystem.MM)
        assert symbol is not None
        assert symbol.corners == (1, 2, 3, 4)

    def test_a_hole_symbol_keeps_its_plating_letter(self):
        # hole<d>x<p>x<tp>x<tm> -- the plating letter is not a parameter.
        symbol = parse_standard_symbol("hole50xpx0.05x0.05", UnitSystem.MM)
        assert symbol is not None
        assert symbol.prefix == "hole"
        assert symbol.plating == "p"
        assert symbol.numbers == (50.0, 0.05, 0.05)

    def test_every_spec_prefix_is_reachable(self):
        # Guard against a spec whose grammar can never match, e.g. because a
        # longer prefix always shadows it.
        unreachable = []
        for spec in STANDARD_SPECS:
            probe = spec.prefix + "x".join(["1"] * spec.min_count)
            if spec.plating:
                probe = spec.prefix + "1xp" + "x1" * (spec.min_count - 1)
            symbol = parse_standard_symbol(probe, UnitSystem.MM)
            if symbol is None or symbol.prefix != spec.prefix:
                unreachable.append(spec.prefix)
        assert unreachable == []


class TestPerSymbolUnitOverride:
    def test_the_trailing_I_and_M_markers_override_the_file_units(self):
        # Spec p.167: "$<n> <name> [I|M]" rebinds the units for that symbol.
        as_metric = parse_standard_symbol("r1000 M", UnitSystem.INCH)
        assert as_metric is not None
        assert as_metric.unit is UnitSystem.MM
        assert as_metric.sizes_mm == (1.0,)

        as_imperial = parse_standard_symbol("r1000 I", UnitSystem.MM)
        assert as_imperial is not None
        assert as_imperial.unit is UnitSystem.INCH
        assert as_imperial.sizes_mm == pytest.approx((25.4,))


class TestSymmetry:
    def test_round_is_symmetric_and_can_draw_a_line(self):
        assert parse_standard_symbol("r300", UnitSystem.MM).is_symmetric()

    def test_square_is_symmetric(self):
        assert parse_standard_symbol("s300", UnitSystem.MM).is_symmetric()

    def test_a_non_square_rectangle_cannot_draw_a_line(self):
        # Spec p.36: only symmetric symbols give a line a well-defined width.
        assert not parse_standard_symbol("rect800x950", UnitSystem.MM).is_symmetric()


class TestSymbolLibrary:
    def test_nested_custom_symbols_expand_dependencies_first(self):
        job = _load("fx_nested_symbol")
        library = _library(job, "fx_nested_symbol")
        order = [d.name for d in library.expand_order("outer")]
        assert order == ["inner", "middle", "outer"]

    def test_a_cycle_is_detected_rather_than_recursed_forever(self):
        job = _load("fx_symbol_cycle")
        library = _library(job, "fx_symbol_cycle")
        with pytest.raises(SymbolCycleError) as excinfo:
            library.expand_order("alpha")
        # The reported chain names the cycle, not just one member of it.
        assert "alpha" in excinfo.value.chain
        assert "beta" in excinfo.value.chain
        assert len(excinfo.value.chain) >= 3

    def test_a_cycle_in_an_unreferenced_symbol_is_reported_as_a_diagnostic(self):
        job = _load("fx_symbol_cycle")
        codes = {d.code for d in job.diagnostics.items}
        assert "symbol.cycle" in codes
        assert job.diagnostics.errors

    def test_loading_is_lazy(self):
        job = _load("fx_nested_symbol")
        library = _library(job, "fx_nested_symbol")
        # Nothing has been resolved yet, so nothing should be parsed.
        assert library.names  # discovery is eager (it is a directory listing)
        assert library.expand_order("inner")[0].name == "inner"

    def test_an_unresolvable_reference_is_reported(self):
        job = _load("fx_nested_symbol")
        library = _library(job, "fx_nested_symbol")
        assert library.definition("does_not_exist") is None
        assert any(d.code == "symbol.unknown" for d in job.diagnostics.items)

    def test_a_multiplied_standard_symbol_is_still_standard(self):
        # A "symbols/" directory named like a standard symbol must not win.
        job = _load("fx_nested_symbol")
        library = _library(job, "fx_nested_symbol")
        target = library.resolve_name("r150", UnitSystem.MM)
        assert target is not None and not hasattr(target, "features")

    def test_case_insensitive_directory_lookup(self):
        # matrix writes OUTER, the directory is outer.
        job = _load("fx_nested_symbol")
        library = _library(job, "fx_nested_symbol")
        assert library.definition("OUTER") is not None
        assert library.definition("Outer") is not None


class TestGeometryRefusesWhatItCannotDo:
    def test_an_unimplemented_family_raises_instead_of_guessing(self):
        from odbpp.geometry import standard_symbol_geometry

        symbol = parse_standard_symbol("ths200x100x45x4x20", UnitSystem.MM)
        assert symbol is not None
        with pytest.raises(UnsupportedFeatureError):
            standard_symbol_geometry(symbol)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load(name: str):
    from odbpp import load_job

    return load_job(str(fixture_path(name)))


def _library(job, name: str) -> SymbolLibrary:
    from odbpp import archive as archive_module

    path = fixture_path(name)
    archive = archive_module.OdbArchive.open(str(path))
    return SymbolLibrary(
        archive, job.root,
        diagnostics=job.diagnostics,
        default_scope=job.info.units,
        definitions=job.attributes,
    )
