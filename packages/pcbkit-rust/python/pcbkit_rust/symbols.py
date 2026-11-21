"""Load the symbol and font definitions used by native artwork."""
from datetime import datetime
import json

from . import _pcbkit_native as native
from .errors import ParseError
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
        self.fonts = {}
        self.text_variables = {}
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

    def preload_fonts(self, layers, job_name):
        requested = set()
        for features in (*layers, *self.files.values()):
            if features is not None:
                requested.update(features.font_references())
        if requested:
            base = f"{self.root}/fonts" if self.root else "fonts"
            for name in self.archive.list_dir(base):
                key = name.casefold()
                path = f"{base}/{name}"
                if key in requested and self.archive.exists(path) and not self.archive.is_dir(path):
                    try:
                        self.fonts[key] = native.Font.parse(self.archive.read_text(path), path)
                    except ValueError as exc:
                        raise ParseError(str(exc)) from exc

        now = datetime.now().astimezone()
        self.text_variables = {
            key: now.strftime(pattern) for key, pattern in {
                "DATE": "%m/%d/%y",
                "DATE-MMDDYY": "%m/%d/%y",
                "DATE-DDMMYY": "%d/%m/%y",
                "DATE-MMDDYYYY": "%m/%d/%Y",
                "DATE-DDMMYYYY": "%d/%m/%Y",
                "DD": "%d", "MM": "%m", "YY": "%y", "YYYY": "%Y",
                "TIME": "%H:%M:%S",
            }.items()
        }
        weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        self.text_variables.update(JOB=job_name, WW=f"{now.isocalendar().week:02d}")
        self.text_variables["WEEK-DAY"] = weekdays[now.weekday()]

    @staticmethod
    def custom_references(features, unit=None):
        return features.custom_references()
