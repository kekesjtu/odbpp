"""Adapt reachable custom-symbol files into the native composer."""
import json

from . import _pcbkit_native as native
from .features import parse_feature_file
from .units import UnitSystem


def describe_symbol(name, unit=UnitSystem.MM):
    description = native.describe_symbol(name, unit.value)
    return None if description is None else json.loads(description)


class SymbolLibrary:
    def __init__(self, archive, root, *, diagnostics, default_scope, definitions=None):
        self.archive = archive
        self.root = root
        self.diagnostics = diagnostics
        self.default_scope = default_scope
        self.definitions = definitions
        self.files = {}
        self._visited = set()
        self._directories = None

    @property
    def directories(self):
        if self._directories is None:
            base = f"{self.root}/symbols" if self.root else "symbols"
            self._directories = {name.casefold(): f"{base}/{name}/features"
                                 for name in self.archive.list_dir(base)
                                 if self.archive.exists(f"{base}/{name}/features")}
        return self._directories

    def preload(self, names):
        pending = list(names)
        while pending:
            name = pending.pop()
            key = name.casefold()
            if key in self._visited:
                continue
            self._visited.add(key)
            path = self.directories.get(key)
            if path is None:
                self.diagnostics.warn("symbol.unknown", f"referenced symbol {name!r} has no symbols/ directory", self.root or None)
                continue
            features = parse_feature_file(self.archive, path,
                default_scope=self.default_scope, diagnostics=self.diagnostics,
                definitions=self.definitions)
            self.files[key] = features
            pending.extend(features.custom_references())

    @staticmethod
    def custom_references(features, unit=None):
        return features.custom_references()
