"""The products have independent runtime dependencies and no cross imports."""
import ast
import importlib.metadata
import json
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

tomllib = pytest.importorskip("tomllib")


@pytest.mark.parametrize("package", ['pcbkit-rust', 'pcbeq-rust'])
def test_distribution_metadata_and_import_boundaries(package):
    root = REPO_ROOT / "packages" / package
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == package
    module = package.replace("-", "_")
    assert project["scripts"] == {package: f"{module}.cli:main"}
    dependencies = {r.split(">=")[0] for r in project["dependencies"]}
    assert "pydantic" in dependencies
    if package != "pcbeq-rust":
        assert "pyclipper" in dependencies
    forbidden = {"pcbkit", "pcbeq", "pcbkit_rust", "pcbeq_rust", "pcbkit-rust", "pcbeq-rust", "odbpp"}-{module}
    assert not (forbidden & dependencies)
    for name in dependencies:
        assert importlib.metadata.version(name)
    source = root / ("python" if package.endswith("-rust") else "src") / module
    for file in source.rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            names = ([node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level
                     else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            assert all(name.split(".")[0] not in forbidden for name in names), file


def test_parser_import_does_not_load_equivalence_dependencies():
    result = subprocess.run([sys.executable, "-c", "import pcbkit_rust,sys,json; print(json.dumps(sorted(sys.modules)))"],
                            check=True, capture_output=True, text=True)
    modules = json.loads(result.stdout)
    assert not any(m.split(".")[0] in {"pcbeq", "numpy", "matplotlib", "shapely"} for m in modules)


def test_contract_python_copies_match_authority():
    from tools.sync_contract import expected_files
    for path, expected in expected_files().items():
        if path.suffix == ".py":
            assert path.read_bytes() == expected, path


def test_equivalence_rejects_odbpp_and_materialmap(tmp_path):
    from pcbeq_rust.cli import main
    assert main(["run", str(REPO_ROOT / "cases/minimal-test-odb"), "-o", str(tmp_path / "out")]) == 2
    invalid = tmp_path / "map.json"
    invalid.write_text('{"regions":[]}', encoding="utf-8")
    assert main(["run", str(invalid), "-o", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out/materialmap.json").exists()
