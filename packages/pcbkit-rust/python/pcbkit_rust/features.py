"""Input adaptation for Rust-owned features and profiles."""
import json

from ._pcbkit_native import Features
from .errors import ParseError


def collect_diagnostics(source, diagnostics):
    for item in json.loads(source.diagnostics_json()):
        diagnostics.add(item["level"], item["code"], item["message"], item["path"] or None)


def parse_feature_file(archive, relpath, *, default_scope, diagnostics,
                       definitions=None, require_units=False):
    types = {name: definition.type.upper() for name, definition in definitions.defs.items()} if definitions else {}
    try:
        features = Features.parse(archive.read_text(relpath), relpath,
                                  default_scope.system.value, types, require_units)
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
    collect_diagnostics(features, diagnostics)
    return features


def parse_profile(archive, relpath, *, default_scope, diagnostics, definitions=None):
    features = parse_feature_file(archive, relpath, default_scope=default_scope,
                                  diagnostics=diagnostics, definitions=definitions)
    if features.surface_count > 1:
        diagnostics.warn("features.multiple_surfaces",
                         f"profile contains {features.surface_count} surfaces; the spec allows one", relpath)
    try:
        return features.profile()
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
