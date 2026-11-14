"""Shared helpers for generated inputs and optional local board samples."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / 'cases'


def case_path(name: str) -> Path:
    """A real-world sample; skipped when the sample has not been fetched."""
    path = CASES / name
    if not path.exists():
        pytest.skip(f'case {name!r} is not present')
    return path


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Materialise ``{relative path: text}`` under *root*."""
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='')
    return root
