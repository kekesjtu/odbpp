"""Run the independently installed pcbeq executable through its public CLI."""
import json
import os
import shutil
import subprocess


def executable(path=None):
    requested = os.fspath(path or os.environ.get("PCBEQ_EXE") or "pcbeq")
    resolved = shutil.which(requested)
    if resolved is None:
        raise RuntimeError(f"pcbeq executable not found: {requested}; put it on PATH or set PCBEQ_EXE")
    return resolved


def run_command(source, output, arguments=(), *, program=None):
    result = subprocess.run(
        [executable(program), "run", os.fspath(source), "-o", os.fspath(output),
         *arguments, "--summary-json"],
        capture_output=True, text=True, encoding="utf-8", shell=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"pcbeq exited with code {result.returncode}")
    return json.loads(result.stdout)


def run(source, output, *, program=None, **options):
    arguments = []
    for name, value in options.items():
        if value is None:
            continue
        flag = {"materials_path": "materials", "materials": "materials-json"}.get(name, name.replace("_", "-"))
        if value is False:
            continue
        arguments.append("--" + flag)
        if value is not True:
            values = value if isinstance(value, (list, tuple)) else [value]
            arguments.extend(json.dumps(v) if isinstance(v, dict) else str(v) for v in values)
    return run_command(source, output, arguments, program=program)
