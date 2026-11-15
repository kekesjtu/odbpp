"""Public diagnostics semantics, including nonblocking errors and publication."""
import json
import os
from pathlib import Path

import pytest

from board_fixture import build_document, rect
from pcbeq_rust.materialmap import read_document
from pcbeq_rust.runner import run


def execute(tmp_path, document, **options):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "out"
    summary = run(source, output, max_cell_mm=[3, 4], **options)
    report = json.loads((output / "diagnostics.json").read_text())
    return summary, report, output


def test_profile_statistics_use_actual_xy_sizes_and_count_reused_geometry_once(tmp_path):
    document = build_document(profiles=[{"id": "step_profile", "polygons": [rect(0, 0, 10, 10), rect(12, 0, 14, 1)]}], layers=[{"name": "A"}, {"name": "B"}])
    summary, report, output = execute(tmp_path, document)
    assert set(report) == {"geometry", "issues", "accuracy", "performance"}
    assert {p.name for p in output.iterdir()} == {"materialmap.json", "diagnostics.json"}
    assert report["geometry"]["profile_count"] == 1
    profile = report["geometry"]["profiles"]["step_profile"]
    assert profile["layer_count"] == 2 and profile["layers"] == ["A", "B"]
    assert profile["region_count"] == 2 and profile["cell_count"] == 13
    assert summary["cells"] == 26 and summary["regions"] == 4
    assert profile["region_size_mm"] == {"min": {"x": 2, "y": 1}, "max": {"x": 10, "y": 10}}
    assert profile["cell_size_mm"]["min"] == {"x": 2, "y": 1}
    assert profile["cell_size_mm"]["max"] == pytest.approx({"x": 2.5, "y": 10 / 3})
    accuracy = report["accuracy"]["profiles"]["step_profile"]
    assert accuracy["area"]["original_mm2"] == 102
    assert accuracy["area"]["relative_error"] == 0 and accuracy["area"]["passed"]
    topology = accuracy["topology"]
    assert set(topology) == {"components", "holes", "positive_width_connections", "preserved"}
    assert topology["components"]["original_count"] == topology["components"]["model_count"] == 2
    assert topology["holes"]["original_count"] == topology["holes"]["model_count"] == 0
    assert topology["preserved"]
    warnings = report["issues"]["warnings"]
    assert len(warnings) == 2 and all(w["layers"] == ["A", "B"] for w in warnings)
    assert report["issues"]["status"] == "completed_with_warnings"
    assert summary["performance"] == report["performance"]
    assert summary["issues"] == report["issues"]
    durations = report["performance"]["stages_s"]
    assert all(value is not None and value >= 0 for value in durations.values())
    assert sum(durations.values()) == pytest.approx(report["performance"]["total_s"], abs=1e-9)


def test_explicit_values_equal_to_builtins_do_not_warn_and_partial_defaults_are_located(tmp_path):
    materials = {"global": {
        "copper": {"k_w_mk": 385, "density_kg_m3": 8960, "specific_heat_j_kgk": 385},
        "base": {"k_w_mk": .3, "density_kg_m3": 1850, "specific_heat_j_kgk": 1100}}}
    document = build_document(layers=[{"name": "A"}])
    _, report, _ = execute(tmp_path, document, materials=materials)
    assert report["issues"] == {"status": "completed", "warnings": [], "errors": []}
    del materials["global"]["base"]["specific_heat_j_kgk"]
    _, report, _ = execute(tmp_path, document, materials=materials)
    warning, = report["issues"]["warnings"]
    assert warning["layers"] == ["A"] and warning["phase"] == "base"
    assert warning["default_values"] == {"specific_heat_j_kgk": 1100}


def test_unknown_thickness_and_propagated_z_are_nonblocking_errors(tmp_path):
    document = build_document(layers=[{"name": "A"}, {"name": "B"}])
    a, b = document["steps"][0]["layers"]
    a["z_mm"] = {"start_mm": 1.0, "thickness_mm": None}
    b["z_mm"] = {"start_mm": None, "thickness_mm": 0.035}
    summary, report, output = execute(tmp_path, document)
    errors = {e["code"]: e for e in report["issues"]["errors"]}
    assert report["issues"]["status"] == "completed_with_errors"
    assert errors["z.thickness_unknown"]["layers"] == ["A"]
    assert errors["z.absolute_position_unknown"]["layers"] == ["B"]
    assert all(not error["blocking"] for error in errors.values())
    delivered = read_document(output / "materialmap.json")
    assert all(r.bounds_mm.z == (None, None) and r.values for r in delivered.regions)
    assert summary["cells"] == 24


def test_image_publication_rolls_back_without_touching_numerical_results(tmp_path, monkeypatch):
    from pcbeq_rust.map_images import render
    from pcbeq_rust import publication
    _, _, output = execute(tmp_path, build_document(layers=[{"name": "A"}]))
    delivered = read_document(output / "materialmap.json")
    old = output / "regions/old.txt"
    old.parent.mkdir()
    old.write_text("previous image set")
    replace = os.replace

    def fail_install(source, target):
        if Path(source).parts[-2:] == ("new", "regions"):
            raise OSError("simulated image publication failure")
        return replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_install)
    with pytest.raises(OSError, match="simulated"):
        render(output / "materialmap.json", output, heatmap=False, grids=False)
    assert old.read_text() == "previous image set"
    assert read_document(output / "materialmap.json") == delivered
    assert json.loads((output / "diagnostics.json").read_text())["issues"]["status"] != "failed"


def test_rerender_replaces_dedicated_image_directories_and_preserves_user_files(tmp_path):
    from pcbeq_rust.map_images import render
    _, _, output = execute(tmp_path, build_document(layers=[{"name": "A"}]))
    render(output / "materialmap.json", output, heatmap=False, panel_width=480)
    (output / "notes.txt").write_text("user notes")
    render(output / "materialmap.json", output, heatmap=False, grids=False)
    assert not (output / "grids").exists()
    assert (output / "regions/regions.png").is_file()
    assert (output / "notes.txt").read_text() == "user notes"
    assert not (output / "diagnostics").exists()


@pytest.mark.parametrize("filename", ["materialmap.json", "diagnostics.json"])
def test_input_is_never_overwritten_by_a_failure_report(tmp_path, filename):
    output = tmp_path / "out"
    output.mkdir()
    source = output / filename
    document = build_document(layers=[{"name": "A"}])
    source.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="input must not be overwritten"):
        run(source, output, max_cell_mm=[3])
    assert json.loads(source.read_text()) == document


def test_invalid_publication_destination_keeps_previous_materialmap_and_reports_failure(tmp_path):
    _, _, output = execute(tmp_path, build_document(layers=[{"name": "A"}]))
    previous = read_document(output / "materialmap.json")
    (output / "index.html").mkdir()
    with pytest.raises(ValueError, match="invalid output destination"):
        run(tmp_path / "input.json", output, max_cell_mm=[1])
    assert read_document(output / "materialmap.json") == previous
    report = json.loads((output / "diagnostics.json").read_text())
    assert report["issues"]["status"] == "failed"
    assert report["issues"]["errors"][-1]["code"] == "publication.failed"
    assert report["performance"]["stages_s"]["publication"] >= 0


@pytest.mark.parametrize("other_layer", [False, True])
def test_missing_layer_profile_is_reported_with_layer_name_before_planning(tmp_path, other_layer):
    document = build_document(layers=([{"name": "VALID"}] if other_layer else []) + [{"name": "MISSING_PROFILE"}])
    document["steps"][0]["profiles"][0]["layers"].remove("MISSING_PROFILE")
    with pytest.raises(ValueError, match='MISSING_PROFILE.*no computational profile'):
        execute(tmp_path, document)
    report = json.loads((tmp_path / "out/diagnostics.json").read_text())
    error, = report["issues"]["errors"]
    assert error["code"] == "prepare.failed" and error["blocking"]
    assert "MISSING_PROFILE" in error["message"]
    assert report["geometry"]["profile_count"] is None
    assert not (tmp_path / "out/materialmap.json").exists()
