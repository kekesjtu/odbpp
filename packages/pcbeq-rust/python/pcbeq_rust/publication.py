"""Publish dedicated image directories and the gallery with rollback."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

IMAGE_OUTPUTS = ("regions", "grids", "heatmaps", "index.html")


class OutputRun:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self._preserve = False

    def __enter__(self):
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        self._temporary = Path(tempfile.mkdtemp(prefix=".pcbeq-images-", dir=self.directory.parent))
        self.path = self._temporary / "new"
        self.path.mkdir()
        return self

    def commit(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in IMAGE_OUTPUTS:
            target = self.directory / name
            if target.is_symlink() or (target.exists() and
                    (not target.is_file() if name == "index.html" else not target.is_dir())):
                raise ValueError(f"invalid image destination: {target}")
        backup = self._temporary / "old"
        moved, installed = [], []
        try:
            for name in IMAGE_OUTPUTS:
                target = self.directory / name
                if target.exists():
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, saved)
                    moved.append(name)
                source = self.path / name
                if source.exists():
                    os.replace(source, target)
                    installed.append(name)
        except BaseException as error:
            try:
                for name in reversed(installed):
                    target = self.directory / name
                    if not target.resolve().is_relative_to(self.directory):
                        raise ValueError(f"unsafe rollback target: {target}")
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                for name in reversed(moved):
                    os.replace(backup / name, self.directory / name)
            except BaseException as rollback_error:
                self._preserve = True
                raise RuntimeError(f"publication failed: {error}; rollback failed: {rollback_error}; backup={backup}") from error
            raise

    def __exit__(self, *exc):
        if not self._preserve and self._temporary.resolve().parent == self.directory.parent:
            shutil.rmtree(self._temporary)
