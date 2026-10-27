"""Build wheels/sdists, install separately, and test JSON-only collaboration."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/distribution-check")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    uv = [shutil.which("uv"), "--cache-dir", str(ROOT / "tmp/uv-cache")]
    if args.offline:
        uv.append("--offline")
    def call(argv, **kwargs):
        return subprocess.run(argv, check=True, cwd=output, **kwargs)
    wheels = output / "dist"
    constraints = output / "constraints.txt"
    constraints.write_text("\n".join(sorted(f"{d.metadata['Name']}=={d.version}"
        for d in importlib.metadata.distributions() if d.metadata["Name"].lower() not in {"pcbkit", "pcbeq", "odbpp"})) + "\n", encoding="utf-8")
    call([*uv, "build", "--all-packages", "--project", str(ROOT), "--out-dir", str(wheels), "--no-sources", "--no-build-logs"])
    executables = {}
    for package in ("pcbkit", "pcbeq"):
        # Rebuild the wheel using just the sdist, away from package source paths.
        sdist = next(wheels.glob(f"{package}-*.tar.gz"))
        call([*uv, "build", str(sdist), "--wheel", "--out-dir", str(output / "rebuilt"), "--no-sources", "--no-build-logs"])
        environment = output / f"env-{package}"
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        if not python.is_file():
            call([*uv, "venv", "--python", sys.executable, str(environment)])
        wheel = next((output / "rebuilt").glob(f"{package}-*.whl"))
        call([*uv, "pip", "install", "--python", str(python), "--constraint", str(constraints),
              "--reinstall-package", package, str(wheel)])
        other = "pcbeq" if package == "pcbkit" else "pcbkit"
        code = f"import importlib.util; import {package}; assert importlib.util.find_spec('{other}') is None"
        if package == "pcbkit":
            code += "; assert importlib.util.find_spec('numpy') is None; assert importlib.util.find_spec('matplotlib') is None"
        call([str(python), "-I", "-c", code])
        executables[package] = str(scripts / (package + (".exe" if os.name == "nt" else "")))
    # Input data are copied and then removed before the consumer starts.
    source = output / "detached-source"
    if source.exists():
        raise ValueError(f"remove the previous detached fixture first: {source}")
    shutil.copytree(ROOT / "cases/minimal-test-odb", source)
    call([executables["pcbkit"], "export", str(source), "-o", str(output / "parser")])
    resolved = source.resolve()
    if not resolved.is_relative_to(output) or resolved == output:
        raise ValueError("unsafe detached fixture cleanup")
    shutil.rmtree(resolved)
    call([executables["pcbeq"], "run", str(output / "parser/intermediate.json"),
          "-o", str(output / "consumer"), "--px", "64", "--py", "64", "--max-cell-mm", "10"])
    call([sys.executable, str(ROOT / "tools/run_pipeline.py"), str(ROOT / "cases/minimal-test-odb"),
          "-o", str(output / "pipeline-case"), "--pcbkit-exe", executables["pcbkit"],
          "--pcbeq-exe", executables["pcbeq"], "--px", "64", "--py", "64", "--max-cell-mm", "10"])
    (output / "validation.json").write_text(json.dumps({"separate_installations": True,
        "sdist_rebuilt": True, "source_removed_before_consumer": True,
        "pipeline": json.loads((output / "pipeline-case/pipeline/run.json").read_text(encoding="utf-8"))},
        indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
