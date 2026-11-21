"""Parser acceptance with authored records and independently expected geometry."""
import math
import json

import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from board_fixture import rect
from conftest import write_tree
from odb_fixture import exported_layers, odb_files, profile_text
from pcbkit_rust.cli import main
from pcbkit_rust import _pcbkit_native as native
from pcbkit_rust.job import load_job
from tools.audit_out import physical_shape


@pytest.mark.parametrize("polarity", ["POSITIVE", "NEGATIVE"])
def test_layer_and_record_polarity_compose_in_file_order(tmp_path, polarity):
    files = odb_files([{"name": "CU", "matrix": {"POLARITY": polarity}, "features": (
        "UNITS=MM\nF 3\n$0 s4000\n$1 s2000\n$2 s1000\n"
        "P 5 5 0 P 0 0\nP 5 5 1 N 0 0\nP 5 5 2 P 0 0\n"
    )}])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    layer = exported_layers(job)["CU"]
    outer, inner, centre = box(3, 3, 7, 7), box(4, 4, 6, 6), box(4.5, 4.5, 5.5, 5.5)
    expected = (outer.difference(inner).union(centre) if polarity == "POSITIVE" else
                box(0, 0, 10, 10).difference(outer).union(inner).difference(centre))
    assert physical_shape(layer["polygons"]).equals(expected)
    for ring in layer["polygons"]:
        assert Polygon(ring["points"]).exterior.is_ccw == (not ring["island"])


def test_matrix_controls_identity_order_and_ignores_unregistered_layers(tmp_path):
    files = odb_files([
        {"name": "F.CU", "matrix": {"ROW": 20}},
        {"name": "DOC", "type": "DOCUMENT", "matrix": {"ROW": 10, "CONTEXT": "MISC"}},
    ])
    # Invalid content would fail parsing if an orphan were incorrectly loaded.
    files["steps/board/layers/orphan/features"] = "UNITS=MM\nP invalid\n"
    job = load_job(str(write_tree(tmp_path / "job", files)))
    step = job.step("board")
    assert step.name == "BOARD" and step.dir_name == "board"
    assert [(layer.name, layer.ref.row) for layer in step.layers] == [("DOC", 10), ("F.CU", 20)]
    assert step.layer("f.cu").dir_name == "f.cu"
    assert step.layer("orphan") is None
    assert [layer.name for layer in job.matrix.board_layers] == ["F.CU"]
    issue, = job.diagnostics.warnings
    assert issue.code == "matrix.orphan_layer_dir"
    assert issue.path.endswith("layers/orphan")
    layers = exported_layers(job)
    assert "DOC" not in layers
    assert layers["F.CU"]["row"] == 20


def test_missing_matrix_rows_use_file_order(tmp_path):
    files = odb_files([{"name": name, "matrix": {"ROW": None}} for name in ("TOP", "BOTTOM")])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    assert [(layer.name, layer.ref.row) for layer in job.single_step.layers] == [("TOP", 1), ("BOTTOM", 2)]


@pytest.mark.parametrize("kind,subtype,declared,form,source,codes", [
    ("SILK_SCREEN", "", None, "RIGID", "DEFAULT", set()),
    ("SIGNAL", "", "RIGID", "RIGID", "DECLARED", set()),
    ("DIELECTRIC", "", None, "RIGID", "DEFAULT", {"matrix.form_missing_dielectric"}),
    ("DIELECTRIC", "", "FLEX", "FLEX", "DECLARED", set()),
    ("SIGNAL", "", "FLEX", "FLEX", "DECLARED", set()),
    ("SIGNAL", "SIGNAL_FLEX", None, "FLEX", "FLEX_SUBTYPE", set()),
    ("POWER_GROUND", "PG_FLEX", None, "FLEX", "FLEX_SUBTYPE", set()),
    ("MIXED", "MIXED_FLEX", None, "FLEX", "FLEX_SUBTYPE", set()),
    ("SIGNAL", "SIGNAL_FLEX", "RIGID", "RIGID", "DECLARED", {"matrix.form_conflict"}),
    ("SIGNAL", "SIGNAL_FLEX", "UNKNOWN", "FLEX", "FLEX_SUBTYPE", {"matrix.unknown_form"}),
    ("SIGNAL", "", "UNKNOWN", "RIGID", "DEFAULT", {"matrix.unknown_form"}),
])
def test_form_precedence_sources_and_diagnostics(tmp_path, kind, subtype, declared, form, source, codes):
    files = odb_files([{"name": "LAYER", "type": kind,
                        "matrix": {"ADD_TYPE": subtype, "FORM": declared}}])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    ref = job.single_step.layers[0].ref
    assert (ref.form.value, ref.form_source.value) == (form, source)
    assert job.diagnostics.codes() == codes
    assert all(issue.level == "warning" and issue.path.endswith("matrix/matrix")
               for issue in job.diagnostics)
    layer = exported_layers(job)["LAYER"]
    assert "form" not in layer and "form_source" not in layer


def test_nested_custom_symbols_expand_after_archive_closes_and_unused_symbols_stay_unread(tmp_path):
    files = odb_files([{"name": "CU", "features": "UNITS=MM\nF 1\n$0 special_outer\nP 5 5 0 P 0 0\n"}])
    files.update({
        "symbols/SPECIAL_OUTER/features": "UNITS=MM\nF 1\n$0 middle\nP 1 0 0 P 0 0\n",
        "symbols/middle/features": (
            "UNITS=MM\nF 2\n$0 inner\n$1 s1000\nP 0 1 0 P 0 0\nP -1 -1 1 P 0 0\n"
        ),
        "symbols/inner/features": profile_text([rect(0, 0, 2, 1)]),
        "symbols/unused/features": "UNITS=MM\nP invalid\n",
    })
    job = load_job(str(write_tree(tmp_path / "job", files)))
    assert set(job.symbols.files) == {"inner", "middle", "special_outer"}
    assert physical_shape(exported_layers(job)["CU"]["polygons"]).equals(
        box(6, 6, 8, 7).union(box(4.5, 3.5, 5.5, 4.5)))
    assert not job.diagnostics.errors


@pytest.mark.parametrize("self_reference", [False, True])
def test_symbol_cycles_are_located_errors_and_cli_fails(tmp_path, capsys, self_reference):
    files = odb_files([{"name": "CU", "features": "UNITS=MM\nF 1\n$0 alpha\nP 5 5 0 P 0 0\n"}])
    child = "ALPHA" if self_reference else "beta"
    files["symbols/alpha/features"] = f"UNITS=MM\nF 1\n$0 {child}\nP 0 0 0 P 0 0\n"
    if not self_reference:
        files["symbols/beta/features"] = "UNITS=MM\nF 1\n$0 ALPHA\nP 0 0 0 P 0 0\n"
    source = write_tree(tmp_path / "job", files)
    job = load_job(str(source))
    issue, = job.diagnostics.errors
    assert issue.code == "symbol.cycle" and issue.path.endswith("symbols")
    cycle, = native.symbol_cycles(job.symbols.files)
    assert [name.lower() for name in cycle] == (["alpha", "alpha"] if self_reference else ["alpha", "beta", "alpha"])
    assert main(["diagnostics", str(source)]) == 1
    assert "symbol.cycle" in capsys.readouterr().out


@pytest.mark.parametrize("units,size,delta,width", [("MM", 1000, 200, 1.2), ("INCH", 40, 10, 1.27)])
@pytest.mark.parametrize("custom", [False, True], ids=["standard", "custom"])
def test_resize_is_a_size_delta_and_reports_unverified_assumption(tmp_path, units, size, delta, width, custom):
    symbol = "custom" if custom else f"s{size}"
    files = odb_files([{"name": "CU", "features": (
        f"UNITS={units}\nF 1\n$0 {symbol}\nP 1 1 -1 0 {delta} P 0 0\n"
    )}], units=units)
    if custom:
        files["symbols/custom/features"] = f"UNITS={units}\nF 1\n$0 s{size}\nP 0 0 0 P 0 0\n"
    job = load_job(str(write_tree(tmp_path / "job", files)))
    record = json.loads(job.single_step.layers[0].features.record_info_json(0))
    scale = 1 if units == "MM" else 25.4
    assert record["resize_mm"] == pytest.approx(.2 if units == "MM" else .254)
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert shape.bounds == pytest.approx((scale - width / 2, scale - width / 2,
                                         scale + width / 2, scale + width / 2), abs=1e-4)
    assert "geometry.resize_unverified" in job.diagnostics.codes()


@pytest.mark.parametrize("orientation,expected", [
    ("0", box(5, 5, 7, 6)),
    ("4", box(3, 5, 5, 6)),
    ("5", box(4, 3, 5, 5)),
    ("8 90", box(5, 3, 6, 5)),
    ("9 90", box(4, 3, 5, 5)),
    ("9 30", Polygon([(5, 5), (5 - math.sqrt(3), 4),
                       (4.5 - math.sqrt(3), 4 + math.sqrt(3) / 2),
                       (4.5, 5 + math.sqrt(3) / 2)])),
])
def test_asymmetric_pad_rotates_then_mirrors_before_translation(tmp_path, orientation, expected):
    files = odb_files([{"name": "CU", "features": (
        f"UNITS=MM\nF 1\n$0 asymmetric\nP 5 5 0 P 0 {orientation}\n"
    )}])
    files["symbols/asymmetric/features"] = profile_text([rect(0, 0, 2, 1)])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    actual = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert actual.symmetric_difference(expected).area < .0002


def standard_shape(tmp_path, symbol, unit="MM"):
    files = odb_files([{"name": "CU", "features": (
        f"UNITS={unit}\nF 1\n$0 {symbol}\nP 0 0 0 P 0 0\n"
    )}], units=unit)
    job = load_job(str(write_tree(tmp_path / "job", files)))
    return physical_shape(exported_layers(job)["CU"]["polygons"])


@pytest.mark.parametrize("symbol,bounds,area,islands,holes", [
    ("el4000x2000", (-2, -1, 2, 1), 2 * math.pi, 1, 0),
    ("bfr4000", (-2, -2, 2, 2), 2 * math.pi, 2, 0),
    ("bfs4000", (-2, -2, 2, 2), 8, 2, 0),
    ("donut_sr4000x2000", (-2, -2, 2, 2), 16 - math.pi, 1, 1),
    ("hole2000xpx200x300", (-1, -1, 1, 1), math.pi, 1, 0),
    ("hole2000xnx200x300", (-1, -1, 1, 1), math.pi, 1, 0),
    ("hole2000xvx200x300", (-1, -1, 1, 1), math.pi, 1, 0),
    ("hplate4000x2000x1000", (-2, -1, 2, 1), 7, 1, 0),
    ("rhplate4000x2000x1000", (-2, -1, 2, 1), 7, 1, 0),
    ("fhplate4000x2000x500x1000", (-2, -1, 2, 1), 7.5, 1, 0),
    ("cross4000x3000x1000x1000x25x50xs", (-2, -1.5, 2, 1.5), 6, 1, 0),
    ("dogbone4000x3000x500x1000x25xs", (-2, -1.5, 2, 1.5), 6, 1, 0),
    ("dpack4000x3000x500x600x3x2", (-2, -1.5, 2, 1.5), 7.2, 6, 0),
])
def test_standard_pad_dimensions_and_topology(tmp_path, symbol, bounds, area, islands, holes):
    shape = standard_shape(tmp_path, symbol)
    parts = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
    assert shape.is_valid
    assert shape.bounds == pytest.approx(bounds, abs=.006)
    assert shape.area == pytest.approx(area, abs=.045)
    assert len(parts) == islands
    assert sum(len(part.interiors) for part in parts) == holes


@pytest.mark.parametrize("symbol,filled,clear,islands", [
    ("thr4000x2000x0x4x500", (1.06, 1.06), (1.5, 0), 4),
    ("ths4000x2000x45x4x500", (1.5, 0), (1.06, 1.06), 4),
    ("s_ths4000x2000x0x4x500", (1.7, 1.7), (1.5, 0), 4),
    ("s_tho4000x2000x45x4x500", (1.5, .2), (1.8, .8), 4),
    ("s_thr4000x2000x45x4x500", (0, 1.5), (1, 1.5), 4),
    ("sr_ths4000x2000x0x4x500", (.85, .85), (.55, .55), 4),
    ("rc_ths6000x4000x45x4x500x500", (0, 1.8), (2.75, 1.75), 4),
    ("rc_tho6000x4000x45x4x500x500", (0, 1.8), (2.8, 1.55), 4),
    ("o_ths6000x4000x0x4x500x500", (1.2, 1.8), (0, 1.75), 4),
    ("oblong_ths6000x4000x45x4x500x500xr", (0, 1.75), (1.8, 1.8), 4),
    ("oblong_ths6000x4000x0x2x500x500xs", (0, 1.75), (2.75, 0), 2),
])
def test_thermal_footprints_preserve_openings(tmp_path, symbol, filled, clear, islands):
    shape = standard_shape(tmp_path, symbol)
    assert shape.is_valid
    assert shape.contains(Point(filled))
    assert not shape.covers(Point(clear))
    assert not shape.covers(Point(0, 0))
    assert len(shape.geoms) == islands


def test_rounded_thermal_ends_keep_the_spoke_clear(tmp_path):
    squared = standard_shape(tmp_path / "squared", "ths4000x2000x0x4x500")
    rounded = standard_shape(tmp_path / "rounded", "thr4000x2000x0x4x500")
    corner = Point(1.8, .36)
    assert squared.contains(corner)
    assert not rounded.covers(corner)
    assert rounded.difference(squared).area < .01


@pytest.mark.parametrize("symbol,filled,clear", [
    ("bfr4000", (-1, 1), (1, 1)),
    ("bfs4000", (1.75, -1.75), (-1, -1)),
    ("hplate4000x2000x1000xra100xro80", (1.7, 0), (1.7, .7)),
    ("rhplate4000x2000x1000xro100", (1.7, .9), (1.7, 0)),
    ("fhplate4000x2000x500x1000xra100", (1.7, .3), (1.7, .9)),
    ("radhplate4000x4000x1000xra200", (1.7, 1.25), (1.7, 0)),
    ("dshape4000x3000x1000xra100", (1.8, 0), (1.8, 1.25)),
    ("cross4000x3000x1000x1000x25x50xrxra100", (-1, 1.25), (0, 1.25)),
    ("dogbone4000x3000x500x1000x25xsx100", (-1, 0), (0, 0)),
    ("dpack4000x3000x500x600x3x2xra150", (0, .9), (0, 0)),
    ("moire100x300x3x100x2200x0", (.247, .247), (.36, .36)),
    ("s_ths4000x2000x0x4x500xr600x1", (-1.9, 1.9), (1.9, 1.9)),
    ("rc_ths6000x4000x0x4x500x500xr800x1", (-2.9, 1.9), (2.9, 1.9)),
])
def test_symbol_parameter_variants_preserve_local_geometry(tmp_path, symbol, filled, clear):
    shape = standard_shape(tmp_path, symbol)
    assert shape.is_valid
    assert shape.contains(Point(filled))
    assert not shape.covers(Point(clear))


def test_moire_rotates_both_crosshair_branches_and_preserves_ring_gaps(tmp_path):
    shape = standard_shape(tmp_path, "moire100x300x1x100x2200x45")
    assert shape.contains(Point(.6, .6))
    assert shape.contains(Point(.6, -.6))
    assert not shape.covers(Point(.8, 0))
    assert len(shape.interiors) == 4


@pytest.mark.parametrize("symbol", ["ext", "ext3"])
def test_null_standard_symbol_has_no_artwork_or_warning(symbol):
    parsed = native.Features.parse(
        f"UNITS=MM\n$0 {symbol}\nP 0 0 0 P 0 0\nL 0 0 1 0 0 P 0\n",
        "features", "MM", {},
    )
    composer = native.Composer({})
    region, _, _ = composer.layer(parsed, None, False, False, 4)
    assert len(region) == 0
    assert json.loads(composer.diagnostics_json()) == []


def test_new_symbols_apply_file_units_and_symbol_overrides(tmp_path):
    shape = standard_shape(tmp_path, "el100x50", "INCH")
    assert shape.bounds == pytest.approx((-1.27, -.635, 1.27, .635), abs=.006)
    metric = standard_shape(tmp_path / "metric", "el2000x1000 M", "INCH")
    assert metric.bounds == pytest.approx((-1, -.5, 1, .5), abs=.006)


@pytest.mark.parametrize("symbol,area,bounds", [
    ("s2000", 4, (4, 4, 6, 6)),
    ("rect2000x1000", 2, (4, 4.5, 6, 5.5)),
    ("r2000", math.pi, (4, 4, 6, 6)),
    ("oval2000x1000", 1 + math.pi / 4, (4, 4.5, 6, 5.5)),
])
def test_basic_standard_pads_have_physical_dimensions(tmp_path, symbol, area, bounds):
    files = odb_files([{"name": "CU", "features": f"UNITS=MM\nF 1\n$0 {symbol}\nP 5 5 0 P 0 0\n"}])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert shape.bounds == pytest.approx(bounds, abs=.01)
    assert shape.area == pytest.approx(area, abs=.03)


@pytest.mark.parametrize("symbol,expected", [
    ("s2000", box(1, 4, 9, 6)),
    ("r2000", box(2, 4, 8, 6).union(unary_union([Point(x, 5).buffer(1, quad_segs=64) for x in (2, 8)]))),
])
def test_line_sweeps_use_symbol_footprint(tmp_path, symbol, expected):
    files = odb_files([{"name": "CU", "features": f"UNITS=MM\nF 1\n$0 {symbol}\nL 2 5 8 5 0 P 0\n"}])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert shape.symmetric_difference(expected).area < .03


def test_full_circle_arc_sweep_is_an_annulus(tmp_path):
    files = odb_files([{"name": "CU", "features": (
        "UNITS=MM\nF 1\n$0 r1000\nA 7 5 7 5 5 5 0 P 0 Y\n"
    )}])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    expected = Point(5, 5).buffer(2.5, quad_segs=128).difference(Point(5, 5).buffer(1.5, quad_segs=128))
    assert shape.symmetric_difference(expected).area < .1
    assert len(shape.interiors) == 1


def test_attribute_tables_resolve_text_at_record_time_and_bare_boolean(tmp_path):
    files = odb_files([{"name": "CU", "features": (
        "UNITS=MM\nF 2\n$0 s1000\n@0 .net_name\n@1 .flag\n&0 first net\n"
        "P 2 2 0 P 0 0;0=0,1\n&0 second net\nP 4 4 0 P 0 0;0=0\n"
    )}])
    files["misc/userattr"] = "TEXT {\nNAME=.net_name\n}\nBOOLEAN {\nNAME=.flag\n}\n"
    job = load_job(str(write_tree(tmp_path / "job", files)))
    first, second = [json.loads(job.single_step.layers[0].features.record_info_json(i)) for i in range(2)]
    assert first["attributes"] == {".net_name": "first net", ".flag": True}
    assert second["attributes"] == {".net_name": "second net"}
    assert "features.bad_attribute_ref" not in job.diagnostics.codes()
