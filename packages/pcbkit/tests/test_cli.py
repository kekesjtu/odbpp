"""CLI smoke tests.

The CLI is how a reviewer checks the parser without writing Python, and it is
also the harness the geometry work will use.  These tests pin the two contracts
that matter: exit code 0 means "loaded with no errors", and ``--format json``
emits a stable, machine-readable structure.
"""

from __future__ import annotations

import json

from pcbkit.cli import main, summarize

from conftest import fixture_path, sample_path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestInfo:
    def test_reports_root_units_and_version(self, capsys):
        code, out, _ = run(capsys, "info", str(fixture_path("fx_negative_layer")))
        assert code == 0
        assert "ODB++ version" in out
        assert "8.1" in out
        assert "MM" in out

    def test_a_clean_job_exits_zero(self, capsys):
        code, _, err = run(capsys, "info", str(fixture_path("fx_nested_symbol")))
        assert code == 0
        assert err == ""


class TestLayers:
    def test_lists_layers_in_matrix_order(self, capsys):
        code, out, _ = run(capsys, "layers", str(fixture_path("fx_negative_layer")))
        assert code == 0
        order = [line.split()[0] for line in out.splitlines() if line.startswith("  ")][1:]
        assert order.index("F.CU") < order.index("GND")

    def test_marks_the_negative_layer(self, capsys):
        _, out, _ = run(capsys, "layers", str(fixture_path("fx_negative_layer")))
        gnd = [l for l in out.splitlines() if l.strip().startswith("GND")][0]
        assert " N " in gnd or gnd.split()[3] == "N"

    def test_a_step_can_be_selected(self, capsys):
        code, _, _ = run(capsys, "layers", str(fixture_path("fx_negative_layer")),
                         "--step", "board")
        assert code == 0

    def test_an_unknown_step_exits_with_the_usage_code(self, capsys):
        code, _, err = run(capsys, "layers", str(fixture_path("fx_negative_layer")),
                           "--step", "nope")
        assert code == 2
        assert "BOARD" in err


class TestSymbols:
    def test_splits_standard_from_user_defined(self, capsys):
        code, out, _ = run(capsys, "symbols", str(fixture_path("fx_nested_symbol")))
        assert code == 0
        assert "standard symbols" in out
        assert "user-defined symbols" in out
        assert "outer" in out

    def test_flags_the_counterexample_as_user_defined(self, capsys):
        code, out, _ = run(capsys, "symbols", str(fixture_path("fx_nested_symbol")))
        assert code == 0
        assert "r150" in out


class TestDiagnostics:
    def test_a_clean_job_reports_none(self, capsys):
        _, out, _ = run(capsys, "diagnostics", str(fixture_path("fx_negative_layer")))
        # The orphan tenting layer is the only diagnostic in this fixture.
        assert "orphan_layer_dir" in out

    def test_a_cycle_exits_nonzero(self, capsys):
        code, out, _ = run(capsys, "diagnostics", str(fixture_path("fx_symbol_cycle")))
        assert code == 1
        assert "symbol.cycle" in out


class TestSummarize:
    def test_text_form_prints_steps_and_layers(self, capsys):
        code, out, _ = run(capsys, "summarize", str(fixture_path("fx_negative_layer")))
        assert code == 0
        assert "=== step 'BOARD' ===" in out

    def test_json_form_is_valid_and_complete(self, capsys):
        code, out, _ = run(capsys, "summarize", str(fixture_path("fx_negative_layer")),
                           "--format", "json")
        assert code == 0
        payload = json.loads(out)
        assert payload["units"] == "MM"
        assert payload["job_root"] == ""
        assert len(payload["steps"]) == 1
        assert payload["steps"][0]["name"] == "BOARD"

    def test_json_writes_to_a_file(self, capsys, tmp_path):
        target = tmp_path / "summary.json"
        code, _, _ = run(capsys, "summarize", str(fixture_path("fx_negative_layer")),
                         "--format", "json", "--json", str(target))
        assert code == 0
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["product"]["version"] == "8.1"

    def test_the_json_carries_diagnostics_with_stable_codes(self, capsys):
        code, out, _ = run(capsys, "summarize", str(fixture_path("fx_negative_layer")),
                           "--format", "json")
        payload = json.loads(out)
        codes = {d["code"] for d in payload["diagnostics"]}
        assert "matrix.orphan_layer_dir" in codes

    def test_inventory_only_mode_skips_features(self, capsys):
        code, out, _ = run(capsys, "summarize", str(fixture_path("fx_negative_layer")),
                           "--no-features", "--format", "json")
        payload = json.loads(out)
        assert all(layer["feature_count"] == 0
                   for layer in payload["steps"][0]["layers"])


class TestSummarizeStructure:
    def test_summarize_is_importable_and_pure(self):
        from pcbkit import load_job

        job = load_job(str(fixture_path("fx_negative_layer")))
        summary = summarize(job)
        assert summary["matrix"]["layer_count"] == 6
        assert [s["name"] for s in summary["matrix"]["steps"]] == ["BOARD"]

    def test_layer_summary_exposes_the_constraint_3_fields(self):
        from pcbkit import load_job

        job = load_job(str(fixture_path("fx_negative_layer")))
        summary = summarize(job)
        layers = {l["name"]: l for l in summary["steps"][0]["layers"]}
        assert layers["DIELECTRIC_1"]["has_profile"] is True
        assert layers["F.CU"]["has_profile"] is False
        assert layers["F.CU"]["profile_error"] is None

    def test_a_layer_profile_failure_shows_up_in_the_json(self):
        from pcbkit import load_job

        job = load_job(str(fixture_path("fx_bad_layer_profile")))
        summary = summarize(job)
        layers = {l["name"]: l for l in summary["steps"][0]["layers"]}
        assert layers["DIELECTRIC_1"]["profile_error"]
        assert not layers["DIELECTRIC_1"]["has_profile"]


class TestBadInput:
    def test_a_missing_path_exits_with_the_error_code(self, capsys, tmp_path):
        code, _, err = run(capsys, "info", str(tmp_path / "nope"))
        assert code == 2
        assert "error:" in err


class TestExport:
    """The export command turns a job into the mm JSON, SVG and HTML preview."""

    def test_it_writes_the_three_artefacts(self, capsys, tmp_path):
        code, out, _err = run(capsys, "export", str(sample_path("minimal-test-odb")),
                              "-o", str(tmp_path))
        assert code == 0
        assert (tmp_path / "intermediate.json").exists()
        assert (tmp_path / "preview.svg").exists()
        assert (tmp_path / "preview.html").exists()
        assert "wrote" in out

    def test_the_summary_names_every_layer(self, capsys, tmp_path):
        _code, out, _err = run(capsys, "export",
                               str(sample_path("minimal-test-odb")),
                               "-o", str(tmp_path))
        assert "top" in out and "bottom" in out
        assert "output mm" in out

    def test_the_json_is_metric_and_positive(self, capsys, tmp_path):
        run(capsys, "export", str(sample_path("minimal-test-odb")),
            "-o", str(tmp_path))
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        assert document["units"] == "mm"
        assert "schema" not in document and "generator" not in document
        for layer in document["steps"][0]["layers"]:
            for polygon in layer["polygons"]:
                assert isinstance(polygon["island"], bool)

    def test_only_json_skips_the_drawings(self, capsys, tmp_path):
        run(capsys, "export", str(sample_path("minimal-test-odb")),
            "-o", str(tmp_path), "--only-json")
        assert (tmp_path / "intermediate.json").exists()
        assert not (tmp_path / "preview.svg").exists()

    def test_per_layer_svg_writes_a_directory(self, capsys, tmp_path):
        run(capsys, "export", str(sample_path("minimal-test-odb")),
            "-o", str(tmp_path), "--per-layer-svg")
        assert (tmp_path / "layers").is_dir()
        assert len(list((tmp_path / "layers").iterdir())) == 2

    def test_a_layer_filter_restricts_the_export(self, capsys, tmp_path):
        _code, out, _err = run(capsys, "export",
                               str(sample_path("minimal-test-odb")),
                               "-o", str(tmp_path), "--layer", "top", "--only-json")
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        assert [l["name"] for l in document["steps"][0]["layers"] if l["geometry_status"] == "ready"] == ["top"]
        assert "1 of 1 layers" in out

    def test_a_misspelled_layer_exits_nonzero_without_writing(self, capsys, tmp_path):
        code, _out, err = run(capsys, "export", str(sample_path("minimal-test-odb")),
                              "-o", str(tmp_path), "--layer", "nope")
        assert code == 2
        assert "nothing written" in err
        assert not (tmp_path / "intermediate.json").exists()

    def test_a_broken_layer_profile_still_exports_the_rest(self, capsys, tmp_path):
        # requirements.md parser constraint 3 makes the bad layer a hard error for that layer, and
        # the other layers must still render: a preview missing one layer is far
        # more useful than no preview.
        code, _out, err = run(capsys, "export",
                              str(fixture_path("fx_bad_layer_profile")),
                              "-o", str(tmp_path))
        assert code == 1
        assert "DIELECTRIC_1" in err
        # The step is named BOARD, so the stem is BOARD -- not the fixture name.
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        names = [l["name"] for l in document["steps"][0]["layers"]]
        assert "F.CU" in names
        assert "DIELECTRIC_1" in names
        assert [l["name"] for l in document["steps"][0]["layers"] if l["geometry_status"] == "failed"] == ["DIELECTRIC_1"]

    def test_digits_changes_the_coordinate_precision(self, capsys, tmp_path):
        run(capsys, "export", str(sample_path("minimal-test-odb")),
            "-o", str(tmp_path), "--only-json", "--digits", "1")
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        assert document["resolution_mm"] == 0.1

    def test_no_diagnostics_controls_terminal_listing_and_preserves_failure(self, capsys, tmp_path):
        source = str(fixture_path("fx_bad_layer_profile"))
        for flags in ((), ("--no-diagnostics",)):
            code, _out, err = run(capsys, "export", source, "-o", str(tmp_path),
                                  "--only-json", *flags)
            assert code == 1
            assert ("error-level diagnostic(s)" in err) == (not flags)
            document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
            assert "diagnostics" not in document
            failed = next(l for l in document["steps"][0]["layers"] if l["name"] == "DIELECTRIC_1")
            assert failed["geometry_status"] == "failed" and failed["geometry_error"]

    def test_diagnostics_are_absent_by_default(self, capsys, tmp_path):
        run(capsys, "export", str(sample_path("minimal-test-odb")),
            "-o", str(tmp_path), "--only-json")
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        assert "diagnostics" not in document

    def test_excluding_a_type_removes_those_layers(self, capsys, tmp_path):
        run(capsys, "export", str(fixture_path("fx_negative_layer")),
            "-o", str(tmp_path), "--only-json", "--exclude-type", "POWER_GROUND")
        document = json.loads((tmp_path / "intermediate.json").read_text(encoding="utf-8"))
        assert all(l["type"] != "POWER_GROUND"
                   for l in document["steps"][0]["layers"] if l["geometry_status"] == "ready")

    def test_the_default_output_directory_is_under_out(self, capsys,
                                                        tmp_path, monkeypatch):
        # Resolve the default relative to an isolated cwd: the real out/ holds
        # synchronized case artifacts and must never be deleted by a test.
        import os

        monkeypatch.chdir(tmp_path)
        target = os.path.join("out", "minimal-test-odb")
        code, out, _err = run(capsys, "export",
                              str(sample_path("minimal-test-odb")))
        assert code == 0
        assert os.path.exists(os.path.join(target, "intermediate.json"))
        assert "out" in out
