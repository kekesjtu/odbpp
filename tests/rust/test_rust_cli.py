"""Process-level Rust commands, Python drawings and product boundaries."""
import json
import subprocess
import sys

import pytest
from conftest import case_path, REPO_ROOT
from tools.run_pipeline import execute

pytest.importorskip("pcbkit_rust")
pytest.importorskip("pcbeq_rust")


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
                 "pcbeq/materialmap.json","pcbeq/index.html","pcbeq/regions/regions.png",
                 "pcbeq/grids/cartesian-grid.png"):
        assert (tmp_path/name).stat().st_size>0
    assert list((tmp_path/"pcbeq/heatmaps").glob("*.png"))


import shutil
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
