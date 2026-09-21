"""Packaging contract: what a plain ``pip install .`` must be able to do.

``pyproject.toml`` used to declare no runtime dependencies at all, with
pyclipper hidden in an optional ``geometry`` extra -- while ``odbpp/__init__.py``
imported the geometry layer unconditionally.  A clean install therefore produced
a package that could not even be imported, let alone run ``info`` or
``summarize``.  These tests pin the declaration, since the local dev environment
has every dependency installed and cannot catch that class of mistake.
"""

from __future__ import annotations

import importlib.metadata

import pytest

from conftest import REPO_ROOT

tomllib = pytest.importorskip("tomllib", reason="needs Python 3.11+ tomllib")


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirement_names(entries) -> set[str]:
    names = set()
    for entry in entries:
        name = entry.split(";")[0]
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
            name = name.split(separator)[0]
        names.add(name.strip().lower())
    return names


class TestRuntimeDependencies:
    def test_importing_the_package_needs_no_extra(self):
        # The import itself is the regression: it must not raise ModuleNotFoundError
        # on an install that skipped every extra.
        import odbpp  # noqa: F401
        import odbpp.clipper  # noqa: F401

    def test_pyclipper_is_a_core_dependency(self):
        project = _pyproject()["project"]
        assert "pyclipper" in _requirement_names(project.get("dependencies", []))

    def test_pyclipper_is_not_also_an_optional_dependency(self):
        # Duplicating it would keep the old, misleading "geometry is optional"
        # story in the metadata.
        extras = _pyproject()["project"].get("optional-dependencies", {})
        for name, entries in extras.items():
            assert "pyclipper" not in _requirement_names(entries), name

    def test_the_declared_core_dependencies_are_installed_here(self):
        # A cheap smoke check that the declaration matches the environment the
        # suite is being run in.
        for name in _requirement_names(_pyproject()["project"]["dependencies"]):
            assert importlib.metadata.version(name)
