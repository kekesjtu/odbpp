"""Shared test helpers.

The synthetic fixtures under ``tests/fixtures/`` are authored in this repository
(see ``tests/fixtures/README.md``); the real boards under ``cases/`` are only
used by tests that skip when the sample is absent, so the suite stays runnable
from a checkout that trimmed the large binaries.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CASES = REPO_ROOT / "cases"


def fixture_path(name: str) -> Path:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture {name!r} is missing")
    return path


def case_path(name: str) -> Path:
    """A real-world sample; skipped when the sample has not been fetched."""
    path = CASES / name
    if not path.exists():
        pytest.skip(f"case {name!r} is not present")
    return path


def sample_path(name: str) -> Path:
    """A hand-authored fixture or a real case, whichever exists.

    Some tests are about *behaviour* rather than about a particular file, and
    read better against a real board than against a fixture built to exercise
    one code path.  Preferring the fixture keeps them running in a trimmed
    checkout; falling back to the case keeps them honest when the samples are
    present.
    """
    path = FIXTURES / name
    if path.exists():
        return path
    return case_path(name)


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Materialise ``{relative path: text}`` under *root*."""
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    return root


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def export_minimal(directory: Path) -> Path:
    """Produce a real JSON input before testing the independent consumer."""
    from pcbkit.cli import main
    target = directory / "parser-input"
    assert main(["export", str(case_path("minimal-test-odb")), "-o", str(target), "--only-json"]) == 0
    return target / "intermediate.json"
