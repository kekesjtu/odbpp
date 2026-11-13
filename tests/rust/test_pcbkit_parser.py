"""Analytical acceptance of native features, whole-layer composition and images."""
import json
from pathlib import Path

import pytest
from PIL import Image

from conftest import fixture_path
from pcbkit_rust import _pcbkit_native as native
from pcbkit_rust import build_step_preview, load_job, step_to_document
from pcbkit_rust.cli import main


def features(body, unit="MM", definitions=None):
    return native.Features.parse(body, "features", unit, definitions or {})


def compose(body, *, definitions=None, profile=None, invert=False, seed=False):
    parsed = features(body)
    composer = native.Composer(definitions or {}, sagitta=.001)
    region, _, _ = composer.layer(parsed, profile, invert, seed, 4)
    return region


def surface(x0, y0, x1, y1, polarity="P", hole=False):
    return f"S {polarity} 0\nOB {x0} {y0} {'H' if hole else 'I'}\nOS {x0} {y1}\nOS {x1} {y1}\nOS {x1} {y0}\nOS {x0} {y0}\nOE\nSE\n"


def test_sparse_symbol_indices_explicit_units_and_typed_live_attributes():
    f = features("""UNITS=INCH
$7 rect1000x2000 M
@0 .label
@1 .flag
@2 .number
&3 'a;b'
P 1 2 7 P 4 9 90;0=3,1,2=17;ID=42
""", definitions={".label": "TEXT", ".flag": "BOOLEAN", ".number": "INTEGER"})
    record = json.loads(f.record_info_json(0))
    assert record["origin_mm"] == pytest.approx([25.4, 50.8])
    assert record["attributes"] == {".label": "a;b", ".flag": True, ".number": 17}
    assert record["uid"] == 42 and record["dcode"] == 4
    assert record["symbol"] == {"index": 7, "name": "rect1000x2000", "units": "MM"}
    region, runs, _ = native.Composer({}).layer(f, None, False, False, 4)
    assert runs == 1 and region.area == pytest.approx(2)
    assert region.bounds == pytest.approx((24.4, 50.3, 26.4, 51.3))


def test_placement_rotates_then_mirrors_then_translates():
    definition = features(surface(0, 0, 1, 2))
    region = compose("UNITS=MM\n$3 asymmetric\nP 10 20 3 P 0 9 90\n", definitions={"asymmetric": definition})
    assert region.bounds == pytest.approx((10, 20, 12, 21))
    assert region.area == pytest.approx(2)
    assert all(not r.is_hole for r in region.rings)


def test_polarity_runs_preserve_later_refill():
    body = surface(0, 0, 10, 10) + surface(2, 2, 8, 8, "N") + surface(4, 4, 6, 6)
    region = compose(body)
    assert region.area == pytest.approx(68)
    assert len(region) == 3
    assert sum(r.is_hole for r in region.rings) == 1


def test_connected_strokes_do_not_cross_polarity_run_boundaries():
    body = surface(-2, -2, 2, 2) + "UNITS=MM\n$0 r1000\nL -1 0 0 0 0 N 0\nL 0 0 1 0 0 P 0\n"
    region = compose(body)
    assert region.area == pytest.approx(15, abs=.002)


def test_connected_strokes_keep_symbol_unit_overrides_separate():
    body = "UNITS=MM\n$0 r1000 M\n$1 r1000 I\nL -1 0 0 0 0 P 0\nL 0 0 1 0 1 P 0\n"
    combined = compose(body)
    large = compose("UNITS=MM\n$0 r1000 I\nL 0 0 1 0 0 P 0\n")
    assert combined.bounds == pytest.approx(large.bounds)
    assert combined.area == pytest.approx(large.area, abs=1e-8)


def test_negative_layer_starts_from_profile_and_inverts_records():
    profile = features(surface(0, 0, 10, 10)).profile()
    region = compose(surface(2, 2, 8, 8) + surface(4, 4, 6, 6, "N"), profile=profile, invert=True, seed=True)
    assert region.area == pytest.approx(68)


def test_recursive_custom_symbols_use_their_own_file_units():
    inner = features("UNITS=INCH\n" + surface(0, 0, 1, 1))
    middle = features("UNITS=MM\n$9 inner\nP 5 0 9 P 0 0\n")
    region = compose("UNITS=MM\n$0 middle\nP 10 0 0 P 0 0\n", definitions={"inner": inner, "middle": middle})
    assert region.area == pytest.approx(25.4 ** 2)
    assert region.bounds == pytest.approx((15, 0, 40.4, 25.4))


def test_cycles_and_depth_fail_with_symbol_names():
    a = features("$0 beta\nP 0 0 0 P 0 0\n")
    b = features("$0 alpha\nP 0 0 0 P 0 0\n")
    assert native.symbol_cycles({"alpha": a, "beta": b}) == [["alpha", "beta", "alpha"]]
    with pytest.raises(ValueError, match="alpha.*beta.*alpha"):
        compose("$0 alpha\nP 0 0 0 P 0 0\n", definitions={"alpha": a, "beta": b})


@pytest.mark.parametrize("clockwise", ["Y", "N"])
def test_closed_arc_sweep_preserves_annular_hole(clockwise):
    region = compose(f"UNITS=MM\n$0 r1000\nA 5 0 5 0 0 0 0 P 0 {clockwise}\n")
    assert region.area == pytest.approx(10 * 3.141592653589793, rel=.005)
    assert len(region) == 2 and sum(r.is_hole for r in region.rings) == 1


def test_square_sweep_keeps_aperture_axis_aligned():
    region = compose("UNITS=MM\n$0 s1000\nL 0 0 5 5 0 P 0\n")
    assert region.area == pytest.approx(11)
    assert region.bounds == pytest.approx((-.5, -.5, 5.5, 5.5))


def test_resize_uses_size_units_and_emits_diagnostic():
    f = features("UNITS=MM\n$0 s1000\nP 0 0 -1 0 100 P 0 0\n")
    composer = native.Composer({})
    region, _, _ = composer.layer(f, None, False, False, 4)
    assert region.bounds == pytest.approx((-.55, -.55, .55, .55))
    assert "geometry.resize_unverified" in composer.diagnostics_json()


@pytest.mark.parametrize("body,message", [
    ("S P 0\nOB 0 0 I\nOS 1 0\n", "unterminated"),
    ("OS 0 0", "outside"),
    ("$0 r1000\nP nan 0 0 P 0 0", "non-finite"),
    ("$0 r1000\nP 0 0 0 P 0 9", "missing numeric"),
    ("$0 'unterminated", "unterminated"),
])
def test_malformed_records_report_source_location(body, message):
    with pytest.raises(ValueError, match=f"features:.*{message}"):
        features(body)


def test_standard_names_require_complete_grammar():
    assert native.describe_symbol("special_0.770x0.840", "MM") is None
    assert native.describe_symbol("rect1000x500xr-100", "MM") is None
    assert json.loads(native.describe_symbol("rect1000x500xr100x12", "MM"))["supported"]
    assert not json.loads(native.describe_symbol("thr1000x500x0x4x100", "MM"))["supported"]


@pytest.mark.parametrize("name,area", [
    ("r2000", 3.141592653589793), ("s2000", 4), ("rect2000x3000", 6),
    ("rect2000x3000xr250", 6 - (4 - 3.141592653589793) * .25 ** 2),
    ("rect2000x3000xc250", 5.875), ("oval3000x2000", 2 + 3.141592653589793),
    ("oval_h3000x2000", (2 + 3.141592653589793) / 2),
    ("di2000x4000", 4), ("tri2000x4000", 4), ("oct2000x2000", 8 * (2 ** .5 - 1)),
    ("hex_l2000x4000", 6), ("hex_s2000x4000", 6),
    ("donut_r2000x1000", 3.141592653589793 * .75), ("donut_s2000x1000", 3),
    ("donut_rc3000x2000x500", 4), ("donut_o3000x2000x500", 1 + 3.141592653589793 * .75),
])
def test_standard_symbol_families_have_analytical_area(name, area):
    region = compose(f"UNITS=MM\n$0 {name}\nP 0 0 0 P 0 0\n")
    assert region.area == pytest.approx(area, rel=.004)


def test_profile_arc_discretisation_uses_requested_tolerance():
    profile = features("S P 0\nOB 1 0 I\nOC -1 0 0 0 Y\nOC 1 0 0 0 Y\nOE\nSE\n").profile()
    coarse = profile.region(.05, 4096)
    fine = profile.region(.0001, 4096)
    assert fine.area == pytest.approx(3.141592653589793, rel=.001)
    assert fine.area > coarse.area


def test_inspection_commands_use_native_record_metadata(capsys):
    path = str(fixture_path("fx_nested_symbol"))
    assert main(["symbols", path]) == 0
    assert "outer" in capsys.readouterr().out
    assert main(["summarize", path, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    layers = data["steps"][0]["layers"]
    assert any(layer["feature_count"] and layer["records"] for layer in layers)


def test_snap_keeps_self_crossings_and_counts_collapsed_rings():
    # A bowtie has zero signed area but positive nonzero-fill area.
    region = native.Region.from_rings([([(0, 0), (2, 2), (0, 2), (2, 0)], False)])
    snapped, count = region.snap(4)
    assert snapped.area == pytest.approx(2) and count == 0
    thin = native.Region.from_rings([([(0, 0), (0, .00001), (1, .00001), (1, 0)], False)])
    assert thin.snap(4)[1] == 1


@pytest.mark.parametrize("name", ["fx_negative_layer", "fx_nested_symbol", "fx_flex_form"])
def test_input_adapter_to_contract_keeps_layer_domains_and_z(name):
    job = load_job(str(fixture_path(name)))
    preview = build_step_preview(job)
    assert not preview.failed_layers
    document = step_to_document(job, preview)
    assert document.steps[0].domains
    assert len(document.steps[0].layers) == len(job.single_step.layers)
    assert any(layer.polygons for layer in document.steps[0].layers)


def test_invalid_layer_profile_has_failed_status():
    job = load_job(str(fixture_path("fx_bad_layer_profile")))
    preview = build_step_preview(job)
    document = step_to_document(job, preview)
    failed = [layer for layer in document.steps[0].layers if layer.geometry_status == "failed"]
    assert failed and all(layer.domain_id is None for layer in failed)


def test_input_adapter_reports_recursive_symbol_cycle():
    job = load_job(str(fixture_path("fx_symbol_cycle")))
    assert "symbol.cycle" in job.diagnostics.codes()


def test_filtered_dielectric_draws_omitted_drills_at_requested_precision(tmp_path):
    from pcbkit_rust.export import PreviewOptions
    from pcbkit_rust.drawing import dielectric_drill_cutouts
    from conftest import write_tree
    import shutil
    source = tmp_path / "case"
    shutil.copytree(fixture_path("fx_negative_layer"), source)
    write_tree(source, {"steps/board/layers/drill_1_2/features":
        "UNITS=MM\n$0 s1000\nP 4.000021 4 0 P 0 0\n"})
    job = load_job(str(source))
    preview = build_step_preview(job, options=PreviewOptions(layers=["dielectric_1"], round_digits=6))
    drawn = dielectric_drill_cutouts(preview)
    assert drawn.drawing_only
    holes = [ring for ring in drawn.layers[0].rings if ring.is_hole]
    assert any(ring.bounds[0] == pytest.approx(3.500021, abs=1e-7) for ring in holes)


def test_png_images_share_viewport_and_preserve_nested_island(tmp_path):
    from pcbkit_rust.png import write_layer_pngs
    from pcbkit_rust.svg import SvgOptions
    from pcbkit_rust.expand import LayerGeometry
    from dataclasses import replace
    job = load_job(str(fixture_path("fx_negative_layer")))
    preview = build_step_preview(job)
    region = compose(surface(0, 0, 10, 10) + surface(2, 2, 8, 8, "N") + surface(4, 4, 6, 6))
    first = replace(preview.layers[0], geometry=LayerGeometry("pattern", region))
    second = replace(preview.layers[1], geometry=LayerGeometry("empty", native.Region()))
    view = replace(preview, layers=[first, second])
    paths = write_layer_pngs(view, tmp_path, SvgOptions(), max_edge=232)
    assert len(paths) == 2
    pattern_path = next(p for p in paths if Path(p).name.startswith(f"{first.layer.ref.row:03d}-"))
    empty_path = next(p for p in paths if p != pattern_path)
    with Image.open(pattern_path) as image, Image.open(empty_path) as empty:
        assert image.size == empty.size == (232, 232)
        assert image.getpixel((36, 196)) != (255, 255, 255)  # outer material
        assert image.getpixel((76, 156)) == (255, 255, 255)  # hole
        assert image.getpixel((116, 116)) != (255, 255, 255)  # nested island
        assert empty.getbbox() == (0, 0, 232, 232)
        assert empty.getextrema() == ((255, 255),) * 3


def test_cli_png_publication_and_json_only_cleanup(tmp_path):
    path = str(fixture_path("fx_nested_symbol"))
    assert main(["export", path, "-o", str(tmp_path), "--per-layer-png"]) == 0
    pngs = list((tmp_path / "layers").glob("*.png"))
    assert len(pngs) == len(load_job(path).single_step.layers)
    owned = json.loads((tmp_path / "diagnostics/artifacts.json").read_text())["files"]
    assert all(p.relative_to(tmp_path).as_posix() in owned for p in pngs)
    notes = tmp_path / "notes.txt"
    notes.write_text("user notes")
    assert main(["export", path, "-o", str(tmp_path), "--only-json", "--per-layer-png"]) == 0
    assert not list((tmp_path / "layers").glob("*.png"))
    assert notes.read_text() == "user notes"
