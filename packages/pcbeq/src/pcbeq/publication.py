"""Stage a complete run and publish only its owned files, with rollback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


class OutputRun:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()

    def __enter__(self):
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix=".pcb-run-", dir=self.directory.parent)
        self.path = Path(self._temporary.name) / "new"
        self.path.mkdir()
        return self

    def commit(self):
        manifest = Path("diagnostics/artifacts.json")
        previous = self.directory / manifest
        old = json.loads(previous.read_text(encoding="utf-8"))["files"] if previous.is_file() else []
        files = sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file())
        (self.path / manifest).parent.mkdir(parents=True, exist_ok=True)
        (self.path / manifest).write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf-8")
        names = sorted(set(old + files + [manifest.as_posix()]))
        # Validate every destination before moving any existing file.
        for name in names:
            relative = Path(name)
            target = self.directory / relative
            if relative.is_absolute() or ".." in relative.parts or not target.resolve().is_relative_to(self.directory):
                raise ValueError(f"unsafe output manifest path: {name}")
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError(f"output destination is not a regular file: {target}")
        backup = Path(self._temporary.name) / "old"
        moved, installed = [], []
        try:
            for name in names:
                target = self.directory / name
                if target.exists():
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, saved)
                    moved.append(name)
                source = self.path / name
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                    installed.append(name)
        except BaseException:
            for name in reversed(installed):
                (self.directory / name).unlink()
            for name in reversed(moved):
                os.replace(backup / name, self.directory / name)
            raise

    def __exit__(self, *exc):
        self._temporary.cleanup()
