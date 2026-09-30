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


def test_sync_out_requires_paired_raster_dimensions(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "100"])
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
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "16", "--py", "16", "--max-cell-mm", "10"])

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
                                      "--max-cell-mm", "10", "--force-equivalent"])
    sync_out.main()
    assert not stale.exists()
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         max_cell_mm=10) is not None

    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "20", "--py", "16",
                                      "--max-cell-mm", "10"])
    sync_out.main()
    changed = json.loads(meta.read_text(encoding="utf-8"))
    assert changed["raster"]["px"] == 20
    assert (changed["grid"]["nx"], changed["grid"]["ny"]) == (10, 8)

    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "16", "--py", "16",
                                      "--max-cell-mm", "25", "20", "--heatmap-grid"])
    sync_out.main()
    coarse = json.loads(meta.read_text(encoding="utf-8"))
    assert (coarse["grid"]["nx"], coarse["grid"]["ny"]) == (4, 4)
    with csv.open(encoding="utf-8", newline="") as handle:
        records = list(csv_module.DictReader(handle))
    assert len(records) == 32
    assert {int(r["pixel_count"]) for r in records} == {16}
    assert "4×4" in (out / "index.html").read_text(encoding="utf-8")
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         max_cell_mm=(25,20), draw_grid=True) is not None
    assert sync_out.existing_equivalence(result, preview, px=16, py=16,
                                         max_cell_mm=(25,20)) is None
    before = csv.stat().st_mtime_ns
    sync_out.main()
    assert csv.stat().st_mtime_ns == before


def test_sync_rejects_invalid_size_bounds(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_out.py", "--px", "100", "--py", "100",
                                      "--min-cell-mm", "6", "--max-cell-mm", "5"])
    with pytest.raises(SystemExit) as exc:
        sync_out.main()
    assert exc.value.code == 2
