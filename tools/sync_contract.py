"""Synchronise the standalone distributions from the one JSON contract source."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def expected_files() -> dict[Path, bytes]:
    source = ROOT / "contracts" / "intermediate.py"
    spec = importlib.util.spec_from_file_location("_pcb_contract", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    schema = module.BoardDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema_bytes = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
    files = {ROOT / "contracts/intermediate.schema": schema_bytes,
             ROOT / "docs/intermediate.schema": schema_bytes}
    for package in ("pcbkit", "pcbeq"):
        files[ROOT / f"packages/{package}/src/{package}/intermediate.py"] = source.read_bytes()
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for path, content in expected_files().items():
        if path.is_file() and path.read_bytes() == content:
            continue
        stale.append(path.relative_to(ROOT).as_posix())
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if args.check and stale:
        print("Contract files need syncing: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
