"""Feature-file record tests.

Pinned here: the two-level attribute indirection (spec p.168), the bare-index
boolean form, layer-vs-record polarity, and the sweep semantics of ``L``/``A``
(a symbol dragged along a path, not a wide stroke).
"""

from __future__ import annotations

import pytest

from odbpp.archive import OdbArchive
from odbpp.features import (
    ArcFeature,
    LineFeature,
    PadFeature,
    SurfaceFeature,
    parse_feature_file,
)
from odbpp.model import Polarity
from odbpp.units import UnitSystem, UnitScope
from odbpp.diagnostics import Diagnostics

from conftest import write_tree

MM = UnitScope(UnitSystem.MM)
INCH = UnitScope(UnitSystem.INCH)


def parse(text: str, *, scope: UnitScope = MM, definitions=None,
          tmp_path=None, diagnostics: Diagnostics | None = None):
    """Parse a features file body from a throwaway archive member."""
    import tempfile
    from pathlib import Path

    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp(prefix="odbpp-features-"))
    root = Path(tmp_path) / "job"
    write_tree(root, {"steps/pcb/layers/x/features": text})
    archive = OdbArchive.open(str(root))
    return parse_feature_file(
        archive, "steps/pcb/layers/x/features",
        default_scope=scope,
        diagnostics=diagnostics if diagnostics is not None else Diagnostics(),
        definitions=definitions,
        require_units=True,
    )


class TestPadRecords:
    def test_position_is_converted_to_millimetres(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r1000\nP 5.5 2.5 0 P 0 0\n")
        pad = parsed.records[0]
        assert isinstance(pad, PadFeature)
        assert (pad.x_mm, pad.y_mm) == (5.5, 2.5)

    def test_inch_positions_convert_through_the_coordinate_semantic(self):
        parsed = parse("UNITS=INCH\nF 1\n$0 r1000\nP 1 2 0 P 0 0\n", scope=INCH)
        pad = parsed.records[0]
        assert (pad.x_mm, pad.y_mm) == (25.4, 50.8)

    def test_symbol_index_selects_from_the_symbol_table(self):
        parsed = parse("UNITS=MM\nF 2\n$0 r1000\n$1 rect500x500\nP 1 1 1 P 0 0\n")
        assert parsed.records[0].symbol.name == "rect500x500"

    def test_record_polarity_overrides_nothing_but_is_recorded(self):
        parsed = parse("UNITS=MM\nF 2\n$0 r1000\nP 1 1 0 P 0 0\nP 2 2 0 N 0 0\n")
        assert [r.polarity for r in parsed.records] == [Polarity.POSITIVE, Polarity.NEGATIVE]

    def test_the_dcode_is_kept_for_tool_lookup(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r1000\nP 1 1 0 P 7 0\n")
        assert parsed.records[0].dcode == 7

    def test_a_resized_pad_form_neg_one_is_recognised(self):
        # apt_def = "-1 <sym_num> <resize_factor>" (spec p.170).
        parsed = parse("UNITS=MM\nF 2\n$0 r1000\n$1 r2000\nP 1 1 -1 1 40 P 0 0\n")
        pad = parsed.records[0]
        assert pad.symbol.name == "r2000"
        assert pad.resize_raw == 40.0

    def test_a_resize_factor_is_a_size_not_a_coordinate(self):
        parsed = parse("UNITS=MM\nF 2\n$0 r1000\n$1 r2000\nP 1 1 -1 1 40 P 0 0\n")
        assert parsed.records[0].resize_mm == pytest.approx(0.040)

    def test_the_specs_own_resize_example(self):
        # Spec p.172: "resized by 0.02 mil (apt_def = -1 0 0.02)".
        parsed = parse("UNITS=INCH\nF 2\n$0 r1000\n$1 r2000\n"
                       "P 1.0 2.0 -1 1 0.02 P 4 1\n", scope=INCH)
        assert parsed.records[0].resize_mm == pytest.approx(0.02 * 0.0254)


class TestOrientationRecording:
    @pytest.mark.parametrize(
        "code,rotation,mirrored",
        [(0, 0.0, False), (1, 90.0, False), (2, 180.0, False), (3, 270.0, False),
         (4, 0.0, True), (5, 90.0, True), (6, 180.0, True), (7, 270.0, True)],
    )
    def test_legacy_codes(self, code, rotation, mirrored):
        parsed = parse(f"UNITS=MM\nF 1\n$0 r1000\nP 1 1 0 P 0 {code}\n")
        orientation = parsed.records[0].orientation
        assert orientation.rotation_deg == rotation
        assert orientation.mirrored is mirrored

    def test_an_arbitrary_angle_form(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r1000\nP 1 1 0 P 0 8 30.0\n")
        orientation = parsed.records[0].orientation
        assert orientation.rotation_deg == pytest.approx(30.0)
        assert orientation.mirrored is False

    def test_a_mirrored_arbitrary_angle_form(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r1000\nP 1 1 0 P 0 9 45.0\n")
        orientation = parsed.records[0].orientation
        assert orientation.rotation_deg == pytest.approx(45.0)
        assert orientation.mirrored is True


class TestLineAndArcSweeps:
    def test_a_line_keeps_its_endpoints_and_symbol(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r200\nL 0 0 10 0 0 P 0\n")
        line = parsed.records[0]
        assert isinstance(line, LineFeature)
        assert line.start_mm == (0.0, 0.0)
        assert line.end_mm == (10.0, 0.0)
        assert line.length_mm == pytest.approx(10.0)
        assert line.symbol.name == "r200"

    def test_an_arc_keeps_its_centre_and_direction(self):
        # A <xs> <ys> <xe> <ye> <xc> <yc> <sym_num> <polarity> <dcode> <cw> (p.172)
        parsed = parse("UNITS=MM\nF 1\n$0 r200\nA 5 0 0 5 0 0 0 P 0 Y\n")
        arc = parsed.records[0]
        assert isinstance(arc, ArcFeature)
        assert (arc.start_mm, arc.end_mm, arc.center_mm) == ((5.0, 0.0), (0.0, 5.0), (0.0, 0.0))
        assert arc.clockwise is True
        assert arc.radius_mm == pytest.approx(5.0)

    def test_a_counter_clockwise_arc(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r200\nA 5 0 0 5 0 0 0 P 0 N\n")
        assert parsed.records[0].clockwise is False

    def test_coincident_arc_endpoints_mean_a_full_circle(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r200\nA 5 0 5 0 0 0 0 P 0 Y\n")
        assert parsed.records[0].is_full_circle is True


class TestSurfaceRecords:
    def test_islands_and_holes_are_preserved(self):
        text = (
            "UNITS=MM\nF 1\nS P 0\n"
            "OB 0 0 I\nOS 10 0\nOS 10 10\nOS 0 10\nOS 0 0\nOE\n"
            "OB 4 4 H\nOS 4 6\nOS 6 6\nOS 6 4\nOS 4 4\nOE\n"
            "SE\n"
        )
        parsed = parse(text)
        surface = parsed.records[0]
        assert isinstance(surface, SurfaceFeature)
        assert [p.is_island for p in surface.polygons] == [True, False]

    def test_a_nested_ob_is_rejected(self):
        text = "UNITS=MM\nF 1\nS P 0\nOB 0 0 I\nOB 1 1 I\n"
        with pytest.raises(Exception):
            parse(text)

    def test_an_ob_outside_a_surface_is_rejected(self):
        with pytest.raises(Exception):
            parse("UNITS=MM\nF 1\nOB 0 0 I\nOS 1 1\n")

    def test_an_invalid_polygon_type_is_rejected(self):
        with pytest.raises(Exception):
            parse("UNITS=MM\nF 1\nS P 0\nOB 0 0 X\n")

    def test_arc_segments_inside_a_polygon_are_kept_as_arcs(self):
        text = (
            "UNITS=MM\nF 1\nS P 0\n"
            "OB 5 0 I\nOC 0 5 0 0 cw\nOS -5 0\nOC 0 -5 0 0 cw\nOS 5 0\nOE\nSE\n"
        )
        parsed = parse(text)
        polygon = parsed.records[0].polygons[0]
        assert polygon.arc_count == 2


class TestAttributeIndirection:
    def test_the_name_table_maps_indices_to_names(self):
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .pad_usage\n@1 .smd\nP 1 1 0 P 0 0;0=0\n"
        )
        assert parsed.attr_names == [".pad_usage", ".smd"]
        assert parsed.records[0].attributes == {".pad_usage": "0"}

    def test_a_bare_index_means_the_boolean_is_true(self):
        # Spec p.178: "BOOLEAN - No value is necessary.  If the index of the
        # attribute is listed, the attribute is set to TRUE."
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .pad_usage\n@1 .smd\nP 1 1 0 P 0 0;0=0,1\n"
        )
        assert parsed.records[0].attributes[".smd"] is True

    def test_a_text_attribute_resolves_through_the_string_table(self):
        from odbpp.model import AttributeDef, AttributeDefinitions

        definitions = AttributeDefinitions(defs={
            ".net_name": AttributeDef(name=".net_name", type="TEXT"),
        })
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .net_name\n&0 GND\nP 1 1 0 P 0 0;0=0\n",
            definitions=definitions,
        )
        assert parsed.records[0].attributes == {".net_name": "GND"}

    def test_gaps_in_the_name_table_are_tolerated(self):
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .a\n@3 .d\nP 1 1 0 P 0 0;3=9\n"
        )
        assert parsed.attr_names == [".a", "", "", ".d"]
        assert parsed.records[0].attributes == {".d": "9"}

    def test_the_tables_are_live_while_records_are_parsed(self):
        # Regression: the tables used to be materialised only after the whole
        # file was read, so every attribute looked undeclared.
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .a\nP 1 1 0 P 0 0;0=1\n"
        )
        assert parsed.records[0].attributes == {".a": "1"}

    def test_an_undeclared_attribute_keeps_its_raw_token_and_is_reported(self):
        diagnostics = Diagnostics()
        parsed = parse(
            "UNITS=MM\nF 1\n$0 r1000\n@0 .mystery\nP 1 1 0 P 0 0;0=42\n",
            diagnostics=diagnostics,
        )
        assert parsed.records[0].attributes == {".mystery": "42"}
        assert any(d.code == "features.undeclared_attribute" for d in diagnostics.items)

    def test_an_out_of_range_index_is_reported_not_silently_dropped(self):
        diagnostics = Diagnostics()
        parse("UNITS=MM\nF 1\n$0 r1000\n@0 .a\nP 1 1 0 P 0 0;5=1\n",
              diagnostics=diagnostics)
        assert any(d.code == "features.bad_attribute_ref" for d in diagnostics.items)

    def test_the_uid_suffix_is_captured(self):
        parsed = parse("UNITS=MM\nF 1\n$0 r1000\nP 1 1 0 P 0 0;;ID=138\n")
        assert parsed.records[0].uid == 138


class TestFileLevelMetadata:
    def test_the_declared_count_is_compared_with_reality(self):
        diagnostics = Diagnostics()
        parse("UNITS=MM\nF 3\n$0 r1000\nP 1 1 0 P 0 0\n", diagnostics=diagnostics)
        parsed = parse("UNITS=MM\nF 3\n$0 r1000\nP 1 1 0 P 0 0\n")
        assert parsed.declared_count == 3
        assert parsed.actual_count == 1

    def test_a_count_mismatch_is_reported(self):
        diagnostics = Diagnostics()
        parse("UNITS=MM\nF 3\n$0 r1000\nP 1 1 0 P 0 0\n", diagnostics=diagnostics)
        assert any(d.code == "features.count_mismatch" for d in diagnostics.items)

    def test_a_missing_units_directive_falls_back_to_the_default(self):
        diagnostics = Diagnostics()
        parsed = parse("F 1\n$0 r1000\nP 1 1 0 P 0 0\n", diagnostics=diagnostics)
        assert parsed.units.system is UnitSystem.MM  # the injected default
        assert parsed.declared_units is None
        assert any(d.code == "features.no_units" for d in diagnostics.items)

    def test_the_file_uid_is_captured(self):
        assert parse("UNITS=MM\nID=400\nF 0\n").uid == 400

    def test_an_unknown_record_type_is_reported(self):
        diagnostics = Diagnostics()
        parse("UNITS=MM\nF 0\nQ 1 2 3\n", diagnostics=diagnostics)
        assert any(d.code == "features.unknown_record" for d in diagnostics.items)


class TestOutOfScopeRecords:
    """T (text) and B (barcode) are deliberately not implemented.

    They need a vector font and a symbology renderer rather than geometry
    expansion.  The requirement is only that a layer containing them stays
    readable and that the omission is *visible*: silently skipping them would
    drop real silkscreen geometry without a trace.
    """

    @pytest.mark.parametrize(
        "record,label",
        [
            ("T 1 1 standard P 0 0.5 0.5 1.0 'REF1' 0", "text"),
            ("B 1 1 UPC39 standard P 0 E 0.008 0.2 Y N Y Y T 'BC1'", "barcode"),
        ],
    )
    def test_the_record_is_reported_by_name_not_as_unknown(self, record, label):
        diagnostics = Diagnostics()
        parse(f"UNITS=MM\nF 2\n$0 r1000\nP 1 1 0 P 0 0\n{record}\n",
              diagnostics=diagnostics)
        codes = {d.code for d in diagnostics.items}
        assert "features.unsupported_record" in codes
        assert "features.unknown_record" not in codes
        assert label in str(diagnostics)

    def test_the_rest_of_the_layer_still_parses(self):
        diagnostics = Diagnostics()
        parsed = parse(
            "UNITS=MM\nF 3\n$0 r1000\n"
            "P 1 1 0 P 0 0\n"
            "T 2 2 standard P 0 0.5 0.5 1.0 'REF1' 0\n"
            "P 3 3 0 P 0 0\n",
            diagnostics=diagnostics,
        )
        assert [r.kind for r in parsed.records] == ["pad", "pad"]

    def test_the_declared_count_is_reported_as_a_mismatch(self):
        # The count in the file includes the records we do not model, so the
        # mismatch warning is expected and is itself the signal.
        diagnostics = Diagnostics()
        parse("UNITS=MM\nF 2\n$0 r1000\nP 1 1 0 P 0 0\n"
              "T 2 2 standard P 0 0.5 0.5 1.0 'X' 0\n", diagnostics=diagnostics)
        assert any(d.code == "features.count_mismatch" for d in diagnostics.items)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
