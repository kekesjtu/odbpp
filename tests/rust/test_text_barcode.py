"""Analytical geometry and file integration for ODB++ labels."""

import json
from zipfile import ZipFile

import pytest
from shapely.geometry import Point, box
from shapely.ops import unary_union

from conftest import write_tree
from odb_fixture import odb_files
from pcbkit_rust import _pcbkit_native as native
from pcbkit_rust import build_step_preview, load_job, step_to_document
from pcbkit_rust.cli import main
from tools.audit_out import physical_shape


FONT = """XSIZE 4
YSIZE 2
OFFSET 1
CHAR A
LINE -1 0 1 0 P S 0.012
LINE 0 -0.5 0 0.5 N S 0.006
ECHAR
CHAR I
LINE 0 0 0 2 P S 0.012
ECHAR
CHAR .
LINE 0 0 0 0 P R 0.012
ECHAR
CHAR '
LINE 0 1 0 2 P S 0.012
ECHAR
"""


def features(body, units="MM"):
    return native.Features.parse(f"UNITS={units}\n{body}", "features", units, {".flag": "BOOLEAN"})


def compose(body, *, variables=None):
    font = native.Font.parse(FONT, "fonts/standard")
    composer = native.Composer({}, sagitta=.0002, fonts={"standard": font})
    region, _, _ = composer.layer(features(body), None, False, False, 5, variables=variables)
    return physical_shape([{"points": r.points, "island": not r.is_hole} for r in region.rings])


def barcode(value, *, origin="0 0", orientation="0", options="N N N N T"):
    return f"B {origin} UPC39 standard P {orientation} E 0.1 2 {options} '{value}'\n"


def test_record_attributes_quoted_text_and_layer_units():
    records = features("@0 .flag\nF 2\n"
                       "T 1 2 Standard N 9 30 .1 .2 1 'A; I' 1;0;ID=42\n"
                       "B 1 2 UPC39 standard P 0 E .008 .2 Y N Y Y B 'A 1';0;ID=43\n",
                       units="INCH")
    assert records.counts_by_kind() == {"text": 1, "barcode": 1}
    assert json.loads(records.diagnostics_json()) == []
    text = json.loads(records.record_info_json(0))
    assert text["text"] == "A; I"
    assert text["font"] == "standard"
    assert text["origin_mm"] == pytest.approx([25.4, 50.8])
    assert text["size_mm"] == pytest.approx([2.54, 5.08])
    assert text["mirrored"] and text["angle"] == 30 and not text["positive"]
    code = json.loads(records.record_info_json(1))
    assert code["size_mm"] == pytest.approx([.2032, 5.08])
    assert [text["uid"], code["uid"]] == [42, 43]
    assert text["attributes"] == code["attributes"] == {".flag": True}


@pytest.mark.parametrize("orientation,version,bounds", [
    ("0", 0, (5, 5, 5.3048, 7.3048)),
    ("0", 1, (6, 5, 6.3048, 7.3048)),
    ("9 90", 0, (2.6952, 4.6952, 5, 5)),
])
def test_text_font_pen_and_insertion_point(orientation, version, bounds):
    shape = compose(f"T 5 5 standard P {orientation} 5 2 1 'I' {version}\n")
    assert shape.equals(box(*bounds))


def test_text_negative_strokes_round_dots_and_spacing():
    body = "T 0 0 standard P 0 5 2 1 'A .' 0\n"
    shape = compose(body)
    assert len(shape.geoms) == 3
    assert shape.contains(Point(.05, .15))
    assert not shape.covers(Point(1.1524, .1524))
    assert shape.bounds[2] > 11
    assert shape.area == pytest.approx((2.3048 - .1524) * .3048 + 3.14159 * .1524**2, abs=.001)


def test_upc39_bar_intervals_and_inverse_background():
    offsets = [(0, 1), (3, 4), (5, 7), (8, 10), (11, 12)]
    a = [(13, 15), (16, 17), (18, 19), (21, 22), (23, 25)]
    intervals = offsets + a + [(x + 26, y + 26) for x, y in offsets]
    expected = unary_union([box(x / 10, 0, y / 10, 2) for x, y in intervals])
    shape = compose(barcode("A"))
    inverse = compose(barcode("A", options="N N Y N T"))
    assert shape.equals(expected)
    assert inverse.equals(box(0, 0, 3.8, 2).difference(expected))


def test_upc39_checksum_full_ascii_and_orientation():
    ordinary = compose(barcode("A"))
    assert compose(barcode("a", options="Y N N N T")).equals(compose(barcode("+A")))
    assert compose(barcode("a", options="Y Y N N T")).equals(compose(barcode("+A8")))
    assert compose(barcode("A", options="N Y N N T")).equals(compose(barcode("AA")))
    mirrored = compose(barcode("A", origin="5 5", orientation="9 90"))
    assert mirrored.bounds == pytest.approx((3, 1.2, 5, 5))
    assert mirrored.area == pytest.approx(ordinary.area)


def test_doubled_quote_encodes_an_apostrophe_in_text_and_full_ascii():
    text = compose("T 0 0 standard P 0 5 2 1 '''' 0\n")
    assert text.equals(box(0, 0, .3048, 1.3048))
    assert compose(barcode("''", options="Y N N N T")).equals(compose(barcode("/G")))


@pytest.mark.parametrize("position,side", [("T", 1), ("B", -1)])
def test_upc39_caption_centres_on_bar_span(position, side):
    artwork = compose(barcode("I", options=f"N N N Y {position}"))
    caption = artwork.intersection(box(0, 2, 3.8, 4) if side == 1 else box(0, -2, 3.8, 0))
    assert caption.area > 0
    assert caption.centroid.x == pytest.approx(1.9)
    assert artwork.intersection(box(0, 0, 3.8, 2)).equals(compose(barcode("I")))


def test_dynamic_barcode_substitutes_names_date_and_origin():
    text = "$$JOB-$$STEP-$$LAYER-$$DATE-MMDDYY-$$X_MM-$$Y_MM"
    variables = {"JOB": "BOARD", "STEP": "PCB", "LAYER": "CU", "DATE-MMDDYY": "11/21/26"}
    actual = compose(barcode(text, origin="1 2"), variables=variables)
    expected = compose(barcode("BOARD-PCB-CU-11/21/26-1-2", origin="1 2"))
    assert actual.equals(expected)


def test_dynamic_font_in_symbol_uses_each_layer_context_after_zip_closes(tmp_path):
    art = "$0 mark\nP 5 5 0 P 0 0\n"
    files = odb_files([
        {"name": "A", "features": art},
        {"name": "I", "features": art, "matrix": {"CONTEXT": "MISC", "TYPE": "DOCUMENT"}},
    ])
    files.update({"fonts/Standard": FONT, "fonts/unused": "invalid unused font",
                  "symbols/mark/features": "UNITS=MM\nT 0 0 STANDARD P 0 5 2 1 '$$LAYER' 0\n"})
    source = tmp_path / "board.zip"
    with ZipFile(source, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    job = load_job(str(source))
    preview = build_step_preview(job)
    assert preview.layer("A").geometry.bounds == pytest.approx((5, 5, 7.3048, 5.3048))
    assert preview.layer("I").drawing.bounds == pytest.approx((5, 5, 5.3048, 7.3048))
    assert [l.name for l in step_to_document(job, preview).steps[0].layers] == ["A"]


def test_negative_label_erases_board_and_misc_and_exports_previews(tmp_path):
    base = "S P 0\nOB 0 0 I\nOS 0 9\nOS 9 9\nOS 9 0\nOS 0 0\nOE\nSE\n"
    text = "T 1 1 standard N 0 5 2 1 'I' 0\n"
    code = barcode("A", origin="2 5")
    artwork = base + text + code.replace("standard P", "standard N")
    files = odb_files([
        {"name": "CU", "features": artwork},
        {"name": "DOC", "features": artwork, "matrix": {"CONTEXT": "MISC", "TYPE": "DOCUMENT"}},
    ])
    files["fonts/standard"] = FONT
    source = write_tree(tmp_path / "board", files)
    destination = tmp_path / "output"
    assert main(["export", str(source), "-o", str(destination), "--per-layer-png"]) == 0
    layers = json.loads((destination / "intermediate.json").read_text())["steps"][0]["layers"]
    copper = physical_shape(layers[0]["polygons"])
    assert len(layers) == 1
    assert not copper.covers(Point(1.15, 2))
    assert copper.covers(Point(1.5, 2))
    assert not copper.covers(Point(2.05, 6))
    assert copper.covers(Point(2.15, 6))
    svg = (destination / "preview.svg").read_text()
    assert '<mask ' in svg and 'fill="black"' in svg
    assert len(list((destination / "layers").glob("*.png"))) == 2


@pytest.mark.parametrize("record,reason", [
    ("T 0 0 standard P 0 1 2 1 'A' 2", "text version"),
    ("T 0 0 standard P 9 45 0 2 1 'A' 1", "dimensions"),
    ("T 0 0 standard P 0 1 2 1 A 0", "single quotes"),
    ("B 0 0 UPC39 standard P 0 E 1 2 N N N X T 'ABC'", "Y or N"),
    ("B 0 0 CODE128 standard P 0 E 1 2 N N N N T 'ABC'", "requires UPC39"),
])
def test_malformed_labels_report_file_and_line(record, reason):
    with pytest.raises(ValueError, match=f"features:2: .*{reason}"):
        features(record)


def test_missing_font_reports_diagnostic_and_unlabelled_barcode_still_draws():
    records = features("T 0 0 absent P 0 5 2 1 'I' 0\n" + barcode("A"))
    composer = native.Composer({})
    region, _, _ = composer.layer(records, None, False, False, 4)
    assert len(region) == 15
    assert [item["code"] for item in json.loads(composer.diagnostics_json())] == ["geometry.font_missing"]


def test_missing_caption_font_does_not_drop_barcode():
    composer = native.Composer({})
    code = features(barcode("I", options="N N N Y B"))
    region, _, _ = composer.layer(code, None, False, False, 4)
    assert len(region) == 15
    assert [item["code"] for item in json.loads(composer.diagnostics_json())] == ["geometry.font_missing"]


def test_missing_glyph_keeps_other_letters_and_reports_the_font():
    font = native.Font.parse(FONT, "fonts/standard")
    composer = native.Composer({}, fonts={"standard": font})
    parsed = features("T 0 0 standard P 0 5 2 1 'IZI' 0\n")
    region, _, _ = composer.layer(parsed, None, False, False, 4)
    assert len(region) == 2
    assert region.bounds == pytest.approx((0, 0, 10.3048, 2.3048))
    [diag] = json.loads(composer.diagnostics_json())
    assert diag["code"] == "geometry.glyph_missing"
    assert "'Z'" in diag["message"]


@pytest.mark.parametrize("value,mode", [("a", "N"), ("é", "Y")])
def test_invalid_barcode_payload_reports_file_and_line(value, mode):
    with pytest.raises(ValueError, match="features:2: .*UPC39"):
        compose(barcode(value, options=f"{mode} N N N T"))
