"""Exercise process boundaries and publication failures with actual deliverables."""
import json
from pathlib import Path

import pytest
from conftest import case_path, export_minimal, REPO_ROOT
from tools.run_pipeline import execute


def test_pipeline_runs_two_processes_and_stops_on_parser_failure(tmp_path):
    output = tmp_path / "good"
    result = execute(case_path("minimal-test-odb"), output,
                     equivalent_args=["--px", "32", "--py", "32", "--max-cell-mm", "10"])
    assert result["status"] == "success"
    assert [s["name"] for s in result["stages"]] == ["pcbkit", "pcbeq"]
    assert all((output / "pcbeq" / p).is_file() for p in ["materialmap.json", "index.html",
        "regions/regions.png", "grids/cartesian-grid.png", "diagnostics/run.json"])
    bad = execute(tmp_path / "missing.tgz", output)
    assert bad["status"] == "failed"
    assert [s["name"] for s in bad["stages"]] == ["pcbkit"]
    assert json.loads((output / "pipeline/run.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_render_failure_preserves_previous_run_and_unrelated_files(tmp_path, monkeypatch):
    from pcbeq.cli import main
    import pcbeq.plotting.regions as regions
    source = export_minimal(tmp_path)
    output = tmp_path / "result"
    argv = ["run", str(source), "-o", str(output), "--px", "32", "--py", "32", "--max-cell-mm", "10"]
    assert main(argv) == 0
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    (output / "notes.txt").write_text("user-owned", encoding="utf-8")
    def fail(*args, **kwargs):
        raise ValueError("injected drawing failure")
    monkeypatch.setattr(regions, "plot_regions", fail)
    assert main(argv) != 0
    assert all((output / p).read_bytes() == data for p, data in before.items())
    assert (output / "notes.txt").read_text(encoding="utf-8") == "user-owned"
    assert not list(tmp_path.glob(".pcb-run-*"))


@pytest.mark.parametrize("package", ["pcbkit", "pcbeq"])
def test_publication_rolls_back_on_install_failure(tmp_path, monkeypatch, package):
    import importlib
    module = importlib.import_module(f"{package}.publication")
    output = tmp_path / "result"
    with module.OutputRun(output) as run:
        (run.path / "one.txt").write_text("old", encoding="utf-8")
        run.commit()
    replace = module.os.replace
    def fail(source, target):
        if Path(source).name == "two.txt" and Path(source).parent.name == "new":
            raise OSError("injected publication failure")
        return replace(source, target)
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        with module.OutputRun(output) as run:
            (run.path / "one.txt").write_text("changed", encoding="utf-8")
            (run.path / "two.txt").write_text("new", encoding="utf-8")
            run.commit()
    assert (output / "one.txt").read_text(encoding="utf-8") == "old"
    assert not (output / "two.txt").exists()


def test_invalid_parser_precision_has_no_traceback(tmp_path):
    from pcbkit.cli import main
    for options in (["--sagitta-mm", "nan"], ["--digits", "-1"]):
        assert main(["export", str(case_path("minimal-test-odb")), "-o", str(tmp_path), *options]) == 2
    assert not (tmp_path / "intermediate.json").exists()
