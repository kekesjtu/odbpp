"""The case synchronizer keeps previews and equivalence images together."""

from __future__ import annotations

import json
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
