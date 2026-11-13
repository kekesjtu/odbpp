"""Process-level Rust commands, Python drawings and product boundaries."""
import json
import subprocess
import shutil
import sys
import os
from pathlib import Path

import pytest
from conftest import case_path, REPO_ROOT
from board_fixture import build_document, rect
from tools.run_pipeline import execute

pytest.importorskip("pcbkit_rust")
pytest.importorskip("pcbeq_rust")


def test_standalone_exe_runs_without_python_or_geos(tmp_path):
    binary = os.environ.get("PCBEQ_EXE")
    assert binary, "build pcbeq and set PCBEQ_EXE before running integration tests"
    isolated = tmp_path / "standalone"
    isolated.mkdir()
    program = isolated / Path(binary).name
    shutil.copy2(binary, program)
    source = isolated / "board.json"
    source.write_text(json.dumps(build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])), encoding="utf-8")
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(("PYTHON", "PYO3", "VIRTUAL_ENV", "PCBEQ"))}
    environment["PATH"] = os.path.join(os.environ.get("SystemRoot", "/"), "System32") if os.name == "nt" else "/usr/bin:/bin"
    result = subprocess.run([str(program), "run", str(source), "-o", str(isolated / "result"),
                             "--max-cell-mm", "5", "--summary-json"],
                            cwd=isolated, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cells"] == 4
    data = json.loads((isolated / "result/materialmap.json").read_text())
    assert data["regions"][0]["values"][0] == [385, 385, 385, 8960, 385]


def test_plot_does_not_require_compute_executable(tmp_path, monkeypatch):
    from pcbeq_rust.cli import main
    from pcbeq_rust.runner import run
    source = tmp_path / "board.json"
    source.write_text(json.dumps(build_document(layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")
    run(source, tmp_path / "numeric", max_cell_mm=[5])
    source.unlink()
    monkeypatch.setenv("PCBEQ_EXE", str(tmp_path / "missing-executable"))
    assert main(["plot", str(tmp_path / "numeric/materialmap.json"), "-o", str(tmp_path / "plots"),
                 "--no-heatmap", "--no-grids"]) == 0
    assert (tmp_path / "plots/regions/regions.png").is_file()


def test_compute_options_are_validated_by_rust(tmp_path):
    from pcbeq_rust.runner import run
    source = tmp_path / "board.json"
    source.write_text(json.dumps(build_document(layers=[{"name": "CU", "polygons": []}])), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be used with"):
        run(source, tmp_path / "numeric", grid_level="standard", max_cell_mm=[5])
    with pytest.raises(ValueError, match="invalid geometry accuracy"):
        run(source, tmp_path / "numeric", max_area_error_ratio=float("nan"))


@pytest.mark.parametrize("package", ["pcbkit", "pcbeq"])
def test_cli_identity_and_independent_import(package):
    module = package + "_rust"
    result = subprocess.run([sys.executable,"-m",module,"--version"],
                            check=True,capture_output=True,text=True)
    assert result.stdout.strip() == package + "-rust 0.1.0"
    result = subprocess.run([sys.executable,"-c",
        f"import {module},sys,json;print(json.dumps(sorted(sys.modules)))"],
        check=True,capture_output=True,text=True)
    assert not any(m.split(".")[0] in {"pcbkit","pcbeq"} for m in json.loads(result.stdout))


def test_rust_pipeline_writes_json_and_python_visualizations(tmp_path):
    executable = lambda name: str(REPO_ROOT / ".venv" / ("Scripts" if sys.platform=="win32" else "bin") / (name+".exe" if sys.platform=="win32" else name))
    result = execute(case_path("minimal-test-odb"),tmp_path,
        pcbkit_exe=executable("pcbkit-rust"),pcbeq_exe=executable("pcbeq-rust"),
        equivalent_args=["--px","97","--py","101","--max-cell-mm","10"])
    assert result["status"] == "success"
    for name in ("pcbkit/preview.svg","pcbkit/preview.html","pcbkit/intermediate.json",
                 "pcbeq/materialmap.json","pcbeq/diagnostics.json","pcbeq/index.html","pcbeq/regions/regions.png",
                 "pcbeq/grids/cartesian-grid.png"):
        assert (tmp_path/name).stat().st_size>0
    assert list((tmp_path/"pcbeq/heatmaps").glob("*.png"))


from tools import sync_out

def test_sync_out_rust_owns_its_pipeline_and_reuses_numeric_and_image_artifacts(tmp_path,monkeypatch):
    pytest.importorskip("pcbkit_rust")
    pytest.importorskip("pcbeq_rust")
    source=tmp_path/"cases/minimal-test-odb"
    source.parent.mkdir()
    shutil.copytree(case_path("minimal-test-odb"),source)
    monkeypatch.setattr(sync_out,"CASES",source.parent)
    monkeypatch.setattr(sync_out,"OUT",tmp_path/"out")
    monkeypatch.setattr(sys,"argv",["sync_out.py","--backend","rust","--px","32","--py","32","--max-cell-mm","10"])
    sync_out.main()
    output=tmp_path/"out/minimal-test-odb/rust"
    materialmap=output/"pcbeq/materialmap.json"
    assert materialmap.is_file() and (output/"pcbeq/index.html").is_file()
    assert not (output.parent/"python").exists()
    before=materialmap.stat().st_mtime_ns
    sync_out.main()
    assert materialmap.stat().st_mtime_ns==before
