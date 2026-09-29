"""The case synchronizer keeps previews and equivalence images together."""

from __future__ import annotations

import json
import csv as csv_module
from pathlib import Path
import shutil
import sys

import pytest

from tools import sync_out
from conftest import case_path


def test_sync_out_requires_raster_dimensions(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_out.py"])
    with pytest.raises(SystemExit) as exc:
        sync_out.main()
    assert exc.value.code == 2


def test_sync_out_writes_and_reuses_all_case_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "cases" / "minimal-test-odb"
    source.parent.mkdir()
    shutil.copytree(case_path("minimal-test-odb"), source)
    out = tmp_path / "out"
    monkeypatch.setattr(sync_out, "ROOT", tmp_path)
    monkeypatch.setattr(sync_out, "CASES", source.parent)
    monkeypatch.setattr(sync_out, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "16", "--py", "16"])

    sync_out.main()
    result = out / "minimal-test-odb"
    preview = result / "pcb.json"
    csv = result / "pcb.MaterialMap.csv"
    meta = result / "pcb.MaterialMap.meta.json"
    assert preview.is_file() and csv.is_file() and meta.is_file()
    assert (result / "pcb.svg").is_file()
    assert (result / "pcb.html").is_file()
    assert (result / "pcb.images.html").is_file()
    assert len(list((result / "masks").glob("*.png"))) == 2
    assert len(list((result / "heatmaps").glob("*.png"))) == 2
    assert json.loads(meta.read_text(encoding="utf-8"))["raster"]["px"] == 16
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "pcb.MaterialMap.csv" in index
    assert "pcb.images.html" in index

    before = csv.stat().st_mtime_ns
    sync_out.main()
    assert csv.stat().st_mtime_ns == before

    stale = result / "masks" / "old-layer.png"
    stale.write_bytes(b"obsolete")
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "16", "--py", "16",
                                      "--force-equivalent"])
    sync_out.main()
    assert not stale.exists()
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         nx=16, ny=16) is not None

    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "20", "--py", "16",
                                      "--nx", "10"])
    sync_out.main()
    changed = json.loads(meta.read_text(encoding="utf-8"))
    assert changed["raster"]["px"] == 20
    assert (changed["grid"]["nx"], changed["grid"]["ny"]) == (10, 16)

    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "16", "--py", "16",
                                      "--coarse-grid", "4"])
    sync_out.main()
    coarse = json.loads(meta.read_text(encoding="utf-8"))
    assert (coarse["grid"]["nx"], coarse["grid"]["ny"]) == (4, 4)
    with csv.open(encoding="utf-8", newline="") as handle:
        records = list(csv_module.DictReader(handle))
    assert len(records) == 32
    assert {int(r["pixel_count"]) for r in records} == {16}
    assert "4×4" in (out / "index.html").read_text(encoding="utf-8")
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         nx=4, ny=4, draw_grid=True) is not None
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         nx=4, ny=4) is None
    before = csv.stat().st_mtime_ns
    sync_out.main()
    assert csv.stat().st_mtime_ns == before


def test_coarse_grid_preserves_hole_boundaries():
    from test_visualize import document, rect, hole

    data = document([], domains=[{
        "id": "step_profile", "polygons": [rect(0, 0, 10, 10), hole(2, 2, 4, 4)]}])
    assert sync_out.coarse_grid_counts(data, 100, 100, 1) == (3, 3)
    assert sync_out.coarse_grid_counts(data, 100, 100, 10) == (10, 10)


def test_coarse_grid_cannot_override_exact_counts(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "100", "--py", "100",
                                      "--coarse-grid", "10", "--nx", "5"])
    with pytest.raises(SystemExit) as exc:
        sync_out.main()
    assert exc.value.code == 2
