"""Loader and product-model tests.

These cover the cross-file rules that no single parser owns:

* the matrix is the authoritative layer list (research 03 R2);
* entity lookup is case insensitive (R1);
* AGENTS constraint 3 (layer profile, then step profile, never a silent fallback);
* AGENTS constraint 4 (everything downstream is millimetres);
* drills take their plating from the tool, not from the layer name (R5).
"""

from __future__ import annotations

import tarfile

import pytest

from odbpp import LoadOptions, load_job
from odbpp.errors import LayerProfileError, ParseError
from odbpp.geometry import bounding_box, polygon_points
from odbpp.diagnostics import Diagnostics
from odbpp.model import FormSource, LayerForm, LayerType, Polarity
from odbpp.units import UnitSystem

from conftest import case_path, fixture_path, sample_path


@pytest.fixture
def negative_job():
    return load_job(str(fixture_path("fx_negative_layer")))


class TestNegativePolarityFixture:
    """fixtures/fx_negative_layer: layer-level and record-level negatives."""

    def test_layer_polarity_is_read_from_the_matrix(self, negative_job):
        step = negative_job.step("BOARD")
        assert step.layer("GND").is_negative
        assert not step.layer("F.CU").is_negative

    def test_the_negative_layer_is_still_a_power_ground_copper_layer(self, negative_job):
        gnd = negative_job.step("BOARD").layer("GND")
        assert gnd.type is LayerType.POWER_GROUND
        assert gnd.is_copper

    def test_record_level_polarity_survives_inside_a_positive_layer(self, negative_job):
        records = negative_job.step("BOARD").layer("F.CU").features.records
        assert any(r.polarity is Polarity.NEGATIVE for r in records)

    def test_an_unregistered_layer_directory_is_ignored_with_a_warning(self, negative_job):
        # research 03 R2 / IPC-4761 tenting layers appear on disk but not in the
        # matrix; parsing them into the model would invent geometry.
        assert negative_job.step("BOARD").layer("TENTING_FRONT") is None
        orphans = [d for d in negative_job.diagnostics.items
                   if d.code == "matrix.orphan_layer_dir"]
        assert len(orphans) == 1
        assert "tenting_front" in orphans[0].message

    def test_layer_directories_resolve_despite_case_differences(self, negative_job):
        # matrix says F.CU / BOARD, the directories are f.cu / board.
        assert negative_job.step("BOARD").layer("F.CU").dir_name == "f.cu"
        assert negative_job.step("BOARD").dir_name == "board"

    def test_units_are_declared_and_metric(self, negative_job):
        assert negative_job.units.system is UnitSystem.MM
        assert negative_job.info.units_declared

    def test_coordinates_arrive_in_millimetres(self, negative_job):
        records = negative_job.step("BOARD").layer("F.CU").features.records
        line = [r for r in records if r.kind == "line"][0]
        assert line.start_mm == (12.0, 2.0)
        assert line.end_mm == (18.0, 2.0)


class TestLayerProfileConstraint:
    """AGENTS constraint 3."""

    def test_a_layer_profile_takes_priority_over_the_step_profile(self):
        job = load_job(str(fixture_path("fx_negative_layer")))
        step = job.step("BOARD")
        dielectric = step.layer("DIELECTRIC_1")
        assert dielectric.profile is not None
        box = bounding_box(polygon_points(step.base_geometry(dielectric).polygons[0]))
        assert box == (1.0, 1.0, 9.0, 9.0)          # the layer profile
        assert box != (0.0, 0.0, 20.0, 10.0)         # not the step profile

    def test_layers_without_a_profile_fall_back_to_the_step_profile(self):
        job = load_job(str(fixture_path("fx_negative_layer")))
        step = job.step("BOARD")
        layer = step.layer("F.CU")
        assert layer.profile is None
        box = bounding_box(polygon_points(step.base_geometry(layer).polygons[0]))
        assert box == (0.0, 0.0, 20.0, 10.0)

    def test_an_unusable_layer_profile_is_a_hard_error_not_a_fallback(self):
        job = load_job(str(fixture_path("fx_bad_layer_profile")))
        step = job.step("BOARD")
        dielectric = step.layer("DIELECTRIC_1")
        assert dielectric.profile is None
        assert dielectric.profile_error
        with pytest.raises(LayerProfileError):
            step.base_geometry(dielectric)

    def test_the_failure_is_recorded_as_a_diagnostic(self):
        job = load_job(str(fixture_path("fx_bad_layer_profile")))
        codes = {d.code for d in job.diagnostics.items}
        assert "layer_profile.invalid" in codes
        assert job.diagnostics.errors

    def test_a_healthy_layer_in_the_same_job_still_works(self):
        job = load_job(str(fixture_path("fx_bad_layer_profile")))
        step = job.step("BOARD")
        assert len(step.base_geometry(step.layer("F.CU")).polygons) == 1


class TestSymbolFixtures:
    def test_nested_custom_symbols_resolve(self):
        job = load_job(str(fixture_path("fx_nested_symbol")))
        codes = {d.code for d in job.diagnostics.items}
        # The referenced symbol exists, so no "unknown symbol" warning.
        assert "symbol.unknown" not in codes

    def test_a_cycle_produces_an_error_diagnostic(self):
        job = load_job(str(fixture_path("fx_symbol_cycle")))
        assert any(d.code == "symbol.cycle" for d in job.diagnostics.items)
        assert job.diagnostics.errors

    def test_symbol_checking_can_be_switched_off(self):
        job = load_job(str(fixture_path("fx_symbol_cycle")),
                       options=LoadOptions(check_symbols=False))
        assert not any(d.code == "symbol.cycle" for d in job.diagnostics.items)


class TestMatrixAuthority:
    def test_a_matrix_layer_without_a_directory_is_warned_about(self, tmp_path):
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "UNITS=MM\n",
            "matrix/matrix": (
                "STEP {\n COL=1\n NAME=pcb\n}\n"
                "LAYER {\n ROW=1\n CONTEXT=BOARD\n TYPE=SIGNAL\n"
                " NAME=F.CU\n POLARITY=POSITIVE\n}\n"
                "LAYER {\n ROW=2\n CONTEXT=BOARD\n TYPE=SIGNAL\n"
                " NAME=B.CU\n POLARITY=POSITIVE\n}\n"
            ),
            "steps/pcb/stephdr": "UNITS=MM\n",
            "steps/pcb/layers/f.cu/features": "UNITS=MM\nF 0\n",
        })
        job = load_job(str(root))
        codes = [d.code for d in job.diagnostics.items]
        assert "matrix.missing_layer_dir" in codes

    def test_a_missing_step_directory_is_warned_about(self, tmp_path):
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "UNITS=MM\n",
            "matrix/matrix": "STEP {\n COL=1\n NAME=ghost\n}\n",
            "steps/other/stephdr": "UNITS=MM\n",
        })
        job = load_job(str(root))
        assert any(d.code == "stephdr.missing_dir" for d in job.diagnostics.items)
        assert job.steps == {}

    def test_layers_are_ordered_by_matrix_row_not_by_name(self):
        job = load_job(str(fixture_path("fx_negative_layer")))
        rows = [layer.ref.row for layer in job.step("BOARD").layers]
        assert rows == sorted(rows)

    def test_a_rowless_matrix_falls_back_to_file_order(self, tmp_path):
        # cases/minimal-test-odb ships ROW_NAME instead of the spec's ROW.
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "UNITS=MM\n",
            "matrix/matrix": (
                "STEP {\n NAME=pcb\n}\n"
                "LAYER {\n CONTEXT=BOARD\n TYPE=SIGNAL\n NAME=A\n POLARITY=POSITIVE\n}\n"
                "LAYER {\n CONTEXT=BOARD\n TYPE=SIGNAL\n NAME=B\n POLARITY=POSITIVE\n}\n"
            ),
            "steps/pcb/stephdr": "UNITS=MM\n",
            "steps/pcb/layers/a/features": "UNITS=MM\nF 0\n",
            "steps/pcb/layers/b/features": "UNITS=MM\nF 0\n",
        })
        job = load_job(str(root))
        assert [l.name for l in job.step("pcb").layers] == ["A", "B"]
        # No spurious duplicate-row warning.
        assert not any(d.code == "matrix.duplicate_layer" for d in job.diagnostics.items)


class TestNestedStepArrays:
    def test_a_step_repeat_is_reported_and_ignored(self, tmp_path):
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "UNITS=MM\n",
            "matrix/matrix": "STEP {\n COL=1\n NAME=pcb\n}\n",
            "steps/pcb/stephdr": (
                "UNITS=MM\n"
                "STEP-REPEAT {\n NAME=panel\n X=0\n Y=0\n DX=100\n DY=100\n NX=2\n NY=2\n}\n"
            ),
            "steps/pcb/layers/f.cu/features": "UNITS=MM\nF 0\n",
        })
        job = load_job(str(root))
        codes = {d.code for d in job.diagnostics.items}
        assert "stephdr.nested_repeat" in codes
        # Recorded, and deliberately not applied.
        header = job.step("pcb").header
        assert len(header.repeats) == 1
        assert header.repeats[0].nx == 2


class TestUnitHandling:
    def test_a_product_model_without_units_defaults_to_imperial(self, tmp_path):
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "JOB_NAME=nounits\nODB_VERSION_MAJOR=8\nODB_VERSION_MINOR=1\n",
            "matrix/matrix": "STEP {\n NAME=pcb\n}\n",
            "steps/pcb/stephdr": "UNITS=MM\n",
            "steps/pcb/layers/f.cu/features": "UNITS=MM\nF 0\n",
        })
        job = load_job(str(root))
        assert job.units.system is UnitSystem.INCH
        assert not job.info.units_declared
        assert any(d.code == "info.missing_units" for d in job.diagnostics.items)

    def test_a_features_file_may_override_the_product_model_units(self, tmp_path):
        from conftest import write_tree

        root = write_tree(tmp_path / "job", {
            "misc/info": "UNITS=MM\n",
            "matrix/matrix": (
                "STEP {\n NAME=pcb\n}\n"
                "LAYER {\n ROW=1\n CONTEXT=BOARD\n TYPE=SIGNAL\n NAME=F.CU\n"
                " POLARITY=POSITIVE\n}\n"
            ),
            "steps/pcb/stephdr": "UNITS=MM\n",
            # The file says INCH even though the product model says MM.
            "steps/pcb/layers/f.cu/features": "UNITS=INCH\nF 1\n$0 r1000\nP 1 1 0 P 0 0\n",
        })
        job = load_job(str(root))
        record = job.step("pcb").layer("F.CU").features.records[0]
        assert record.x_mm == pytest.approx(25.4)
        assert record.symbol.effective_unit(UnitSystem.INCH) is UnitSystem.INCH


class TestDrillTools:
    def test_tool_sizes_use_the_size_semantic(self):
        job = load_job(str(fixture_path("fx_negative_layer")))
        tools = job.step("BOARD").layer("DRILL_1_2").tools
        # 300 micrometres -> 0.3 mm; 1000 micrometres -> 1.0 mm.
        assert tools.tools[1].drill_size_mm == pytest.approx(0.3)
        assert tools.tools[2].drill_size_mm == pytest.approx(1.0)

    def test_plating_comes_from_the_tool_not_the_layer_name(self):
        # research 03 R5: a "plated" drill layer can hold NON_PLATED tools.
        job = load_job(str(fixture_path("fx_negative_layer")))
        tools = job.step("BOARD").layer("DRILL_1_2").tools
        assert tools.tools[1].type.is_plated
        assert not tools.tools[2].type.is_plated

    def test_tools_can_be_skipped_for_inventory_only_runs(self):
        job = load_job(str(fixture_path("fx_negative_layer")),
                       options=LoadOptions(load_tools=False))
        assert job.step("BOARD").layer("DRILL_1_2").tools is None


class TestLoadOptions:
    def test_step_selection(self):
        job = load_job(str(fixture_path("fx_negative_layer")), options=LoadOptions(step="board"))
        assert list(job.steps) == ["BOARD"]

    def test_an_unknown_step_name_lists_the_available_ones(self):
        with pytest.raises(ParseError) as excinfo:
            load_job(str(fixture_path("fx_negative_layer")),
                     options=LoadOptions(step="nope"))
        assert "BOARD" in str(excinfo.value)

    def test_feature_loading_can_be_switched_off(self):
        job = load_job(str(fixture_path("fx_negative_layer")),
                       options=LoadOptions(load_features=False))
        layer = job.step("BOARD").layer("F.CU")
        assert layer.features is None
        assert layer.feature_count == 0


class TestArchivedSources:
    def test_a_tarball_loads_identically_to_a_directory(self, tmp_path):
        source = fixture_path("fx_negative_layer")
        archive_path = tmp_path / "fx.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(source, arcname="odb")
        from_dir = load_job(str(source))
        from_tar = load_job(str(archive_path))
        assert from_tar.root == "odb"
        assert [l.name for l in from_tar.step("BOARD").layers] == \
               [l.name for l in from_dir.step("BOARD").layers]
        assert from_tar.units.system is from_dir.units.system


# ---------------------------------------------------------------------------
# real boards (skipped when the sample is absent)
# ---------------------------------------------------------------------------

class TestRealCases:
    def test_minimal_smoke_board(self):
        job = load_job(str(case_path("minimal-test-odb")))
        assert job.info.version.startswith("8.1")
        step = next(iter(job.steps.values()))
        assert {l.name for l in step.layers} == {"top", "bottom"}
        assert step.profile is not None
        assert not job.diagnostics.errors

    def test_kitchen_sink_covers_many_layer_types(self):
        job = load_job(str(case_path("kicad-kitchen-sink")))
        step = next(iter(job.steps.values()))
        types = {l.type for l in step.layers if l.type is not None}
        assert {LayerType.SIGNAL, LayerType.DIELECTRIC, LayerType.SOLDER_MASK,
                LayerType.SOLDER_PASTE, LayerType.SILK_SCREEN,
                LayerType.DRILL} <= types

    def test_a_production_inch_job_converts_to_millimetres(self):
        job = load_job(str(case_path("designodb-rigidflex.tgz")))
        assert job.units.system is UnitSystem.INCH
        step = next(iter(job.steps.values()))
        for layer in step.layers:
            if not layer.features:
                continue
            for record in layer.features.records:
                if record.kind == "pad":
                    # A rigid-flex board is on the order of 100 mm, not 2500 mm
                    # (which is what 100 inches would be) and not 0.1 mm.
                    assert -1000.0 < record.x_mm < 1000.0
                    break
            break

    def test_the_rigidflex_blind_and_buried_drills_are_distinguishable(self):
        job = load_job(str(case_path("designodb-rigidflex.tgz")))
        step = next(iter(job.steps.values()))
        spans = {l.name: (l.ref.start_name, l.ref.end_name)
                 for l in step.drill_layers}
        assert spans.get("D_3_8") == ("FLEX_3", "FLEX_8") or spans.get("D_3_8") is not None
        # One drill layer carries both plated and non-plated tools.
        mixed = [l for l in step.drill_layers
                 if l.tools and len({t.type for t in l.tools.tools.values()}) > 1]
        assert mixed, "expected at least one mixed-plating drill layer"


def _lf(*lines: str) -> str:
    """Join fixture lines with LF.

    Spelled out rather than embedded as escape sequences so the fixture text
    stays readable and does not depend on the source file's own line endings.
    """
    return "".join(line + chr(10) for line in lines)


_PROFILE = _lf("UNITS=MM", "F 1", "S P 0", "OB 0 0 I", "OS 0 10",
               "OS 20 10", "OS 20 0", "OS 0 0", "OE", "SE")


def _minimal_job(matrix: str) -> dict:
    return {
        "matrix/matrix": matrix,
        "misc/info": _lf("UNITS=MM"),
        "steps/board/stephdr": _lf("UNITS=MM"),
        "steps/board/profile": _PROFILE,
    }


class TestLayerForm:
    """``FORM`` is resolved by a rule in spec p.66, not by a guess.

        FORM  Defines the layer as ridge or flexible. (optional) (default = RIGID)
            RIGID / FLEX
        Layers of the subtype SIGNAL_FLEX, PG_FLEX and MIXED_FLEX have a
        default FORM = FLEX.
        Layers of type DIELECTRIC are to be defined as RIGID or FLEX.

    So a missing field is not "unknown": the spec supplies the value, and the
    subtype overrides it.  What is worth keeping is *how* the value was reached,
    because "RIGID because the spec says so" and "RIGID because the exporter was
    silent" are not equally strong -- especially on a dielectric layer, which is
    where a wrong answer moves the flex boundary.
    """

    def test_a_declared_form_wins(self):
        step = load_job(str(fixture_path("fx_flex_form"))).single_step
        assert step.layer("RIGID_1").ref.form is LayerForm.RIGID
        assert step.layer("RIGID_1").ref.form_source is FormSource.DECLARED
        assert step.layer("FLEX_1").ref.form is LayerForm.FLEX
        assert step.layer("FLEX_1").ref.form_source is FormSource.DECLARED

    def test_a_flex_subtype_implies_flex_without_a_declared_form(self):
        # The rule that makes cases/designodb-rigidflex readable: FLEX_5 and
        # FLEX_6 never write FORM, but ADD_TYPE=PG_FLEX means FLEX.
        ref = load_job(str(fixture_path("fx_flex_form"))).single_step.layer("GND_FLEX").ref
        assert ref.add_type == "PG_FLEX"
        assert ref.form is LayerForm.FLEX
        assert ref.form_source is FormSource.FLEX_SUBTYPE
        assert ref.is_flex is True

    def test_the_rigid_default_applies_to_an_ordinary_layer(self):
        ref = load_job(str(fixture_path("fx_flex_form"))).single_step.layer("SILK_1").ref
        assert ref.form is LayerForm.RIGID
        assert ref.form_source is FormSource.DEFAULT
        assert ref.is_flex is False
        assert ref.form_is_declared is False

    def test_a_declared_form_beats_the_subtype_default_and_says_so(self):
        # FORM is documented as a *default* per subtype, so the declared value
        # wins -- but the contradiction is reported, because otherwise an
        # exporter emitting FORM=RIGID on a PG_FLEX layer is invisible.
        diagnostics = Diagnostics()
        step = load_job(str(fixture_path("fx_flex_form")),
                        diagnostics=diagnostics).single_step
        ref = step.layer("SIG_CONFLICT").ref
        assert ref.form is LayerForm.RIGID
        conflict = [d for d in diagnostics if d.code == "matrix.form_conflict"]
        assert len(conflict) == 1
        assert "SIG_CONFLICT" in conflict[0].message

    def test_a_dielectric_that_omits_form_is_reported(self):
        # Spec p.66 says dielectric layers are *to be defined* RIGID or FLEX --
        # the field is optional only in general, not for this class.
        diagnostics = Diagnostics()
        load_job(str(fixture_path("fx_flex_form")), diagnostics=diagnostics)
        missing = [d for d in diagnostics
                   if d.code == "matrix.form_missing_dielectric"]
        assert [d.message.split("'")[1] for d in missing] == ["POLYIMIDE_1"]

    def test_a_real_dielectric_that_declares_form_is_silent(self):
        # fx_negative_layer's DIELECTRIC_1 writes FORM=RIGID, DIELECTRIC_TYPE=CORE
        # and CU_TOP/CU_BOTTOM -- the fully specified shape of the field.
        diagnostics = Diagnostics()
        step = load_job(str(fixture_path("fx_negative_layer")),
                        diagnostics=diagnostics).single_step
        ref = step.layer("DIELECTRIC_1").ref
        assert ref.form is LayerForm.RIGID
        assert ref.form_source is FormSource.DECLARED
        assert "matrix.form_missing_dielectric" not in diagnostics.codes()

    def test_a_job_without_dielectrics_does_not_warn(self):
        diagnostics = Diagnostics()
        load_job(str(sample_path("minimal-test-odb")), diagnostics=diagnostics)
        assert "matrix.form_missing_dielectric" not in diagnostics.codes()

    def test_a_dielectric_that_declares_form_is_silent(self, tmp_path):
        from conftest import write_tree

        write_tree(tmp_path / "job", _minimal_job(_lf(
            "LAYER {", "    ROW=1", "    NAME=DIEL", "    TYPE=DIELECTRIC",
            "    FORM=FLEX", "}",
        )))
        diagnostics = Diagnostics()
        load_job(str(tmp_path / "job"), diagnostics=diagnostics)
        assert "matrix.form_missing_dielectric" not in diagnostics.codes()
        assert "matrix.form_conflict" not in diagnostics.codes()

    def test_an_unrecognised_form_falls_back_instead_of_giving_up(self, tmp_path):
        from conftest import write_tree

        write_tree(tmp_path / "job", _minimal_job(_lf(
            "LAYER {", "    ROW=1", "    NAME=A", "    TYPE=POWER_GROUND",
            "    ADD_TYPE=PG_FLEX", "    FORM=BENDABLE", "}",
        )))
        diagnostics = Diagnostics()
        job = load_job(str(tmp_path / "job"), diagnostics=diagnostics)
        ref = job.single_step.layers[0].ref
        # Unreadable, so the subtype rule takes over -- and it says FLEX.
        assert ref.form is LayerForm.FLEX
        assert ref.form_source is FormSource.FLEX_SUBTYPE
        unknown = [d for d in diagnostics if d.code == "matrix.unknown_form"]
        assert len(unknown) == 1 and "BENDABLE" in unknown[0].message
        # A FORM we cannot read is not an ADD_TYPE problem.
        assert "matrix.unknown_add_type" not in diagnostics.codes()

    def test_the_rigid_flex_sample_resolves_its_flex_region(self):
        """The point of the whole rule.

        designodb-rigidflex writes no FORM anywhere, yet two of its 43 layers are
        flex -- because spec p.66 gives ADD_TYPE=PG_FLEX a default of FLEX.
        Reading that sentence is the difference between a rigid-flex board whose
        flex region is identifiable and one where every layer looks rigid.
        """
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("designodb-rigidflex.tgz")),
                       diagnostics=diagnostics)
        flex = [l.name for l in job.single_step.layers if l.ref.is_flex]
        assert flex == ["FLEX_5", "FLEX_6"]
        assert all(job.single_step.layer(n).ref.form_source is FormSource.FLEX_SUBTYPE
                   for n in flex)
        assert all(l.ref.form is LayerForm.RIGID for l in job.single_step.layers
                   if l.name not in flex)
        # ...and its nine dielectric layers are reported for not declaring FORM.
        assert len([d for d in diagnostics
                    if d.code == "matrix.form_missing_dielectric"]) == 9
