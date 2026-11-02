"""Keep the sole benchmark bounded to JSON input through material file output."""
import json
import sys

import pytest

from pcbeq.materialmap import read_document
from test_equiv import build_document, rect
from tools import benchmark_pcbeq as benchmark


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "intermediate.json"
    path.write_text(json.dumps(build_document(layers=[
        {"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])), encoding="utf-8")
    return path


def arguments(source, output):
    return [str(source), "--output", str(output), "--px", "32", "--py", "32",
            "--max-cell-mm", "5"]


def test_benchmark_delivers_materialmap_without_visualization(source, tmp_path, monkeypatch):
    # Even importing a rendering module must be unnecessary for this benchmark.
    for name in ("pcbeq.visualize", "pcbeq.plotting.regions", "pcbeq.plotting.grids", "matplotlib"):
        monkeypatch.setitem(sys.modules, name, None)
    reads = []
    original = benchmark.read_document

    def record_read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(benchmark, "read_document", record_read)
    output = tmp_path / "benchmark"
    assert benchmark.main(arguments(source, output) + ["--repeat", "2", "--warmup", "1"]) == 0
    assert reads == [source] * 3
    assert {p.name for p in output.iterdir()} == {"materialmap.json", "benchmark.json"}
    document = read_document(output / "materialmap.json")
    assert len(document.regions) == 1
    assert document.regions[0].values == [(385, 385, 385, 8960, 385)] * 4
    report = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
    assert len(report["runs"]) == 2  # Warmup is excluded from statistics.
    for run in report["runs"]:
        assert (run["cells"], run["regions"], run["layers"], run["raster"]) == (4, 1, 1, [32, 32])
        times = run["durations_s"]
        assert times["total"] == pytest.approx(times["read_json"] + times["equivalence_and_write"])
        assert times["total"] > 0 and run["cpu_s"] >= 0


def test_timing_includes_json_read_and_materialmap_close(source, tmp_path, monkeypatch):
    clock = [0.0]
    original_read = benchmark.read_document
    original_writer = benchmark.MaterialMapWriter

    def slow_read(path):
        clock[0] += 2
        return original_read(path)

    class SlowCloseWriter(original_writer):
        def __exit__(self, *args):
            super().__exit__(*args)
            clock[0] += 3

    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(benchmark, "read_document", slow_read)
    monkeypatch.setattr(benchmark, "MaterialMapWriter", SlowCloseWriter)
    result = benchmark.measure(source, tmp_path, benchmark.GridOptions(px=32, py=32), {})
    assert result["durations_s"] == {"read_json": 2, "equivalence_and_write": 3, "total": 5}


def test_failed_equivalence_preserves_previous_map_and_cleans_partial_file(source, tmp_path, monkeypatch):
    output = tmp_path / "failed"
    output.mkdir()
    final = output / "materialmap.json"
    final.write_bytes(b"previous result")

    def fail(*args, **kwargs):
        raise benchmark.EquivalenceError("injected failure")

    monkeypatch.setattr(benchmark, "equivalence_from_document", fail)
    assert benchmark.main(arguments(source, output) + ["--warmup", "0", "--repeat", "1"]) == 2
    assert final.read_bytes() == b"previous result"
    assert not (output / "materialmap.json.tmp").exists()
    assert not (output / "benchmark.json").exists()


@pytest.mark.parametrize("options", [["--repeat", "0"], ["--warmup", "-1"],
                                    ["--px", "4"], ["--max-cell-mm", "nan"],
                                    ["--grid-level", "fine", "--max-cell-mm", "5"]])
def test_invalid_benchmark_settings_do_not_produce_outputs(source, tmp_path, options):
    output = tmp_path / "invalid"
    args = [str(source), "--output", str(output), *options]
    try:
        code = benchmark.main(args)
    except SystemExit as exc:
        code = exc.code
    assert code == 2
    assert not output.exists()
