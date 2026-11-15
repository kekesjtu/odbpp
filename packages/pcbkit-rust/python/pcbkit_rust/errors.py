"""Exception hierarchy for the ODB++ parser.

Input-adapter errors derive from :class:`OdbError` and carry source context.
The native API reports invalid records and geometry through ValueError.
"""

from __future__ import annotations

from typing import Optional


class OdbError(Exception):
    """Base class for every error raised by this package."""


class ArchiveError(OdbError):
    """The input is not a readable ODB++ tree/archive, or has no job root."""


class ParseError(OdbError):
    """A file could not be parsed.

    Parameters
    ----------
    message:
        Human readable description of what went wrong.
    path:
        Path of the offending file, relative to the job root.
    line_no:
        One-based line number inside *path*, when known.
    line:
        The raw offending line, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Optional[str] = None,
        line_no: Optional[int] = None,
        line: Optional[str] = None,
    ) -> None:
        self.message = message
        self.path = path
        self.line_no = line_no
        self.line = line

        where = ""
        if path is not None:
            where = f" [{path}"
            if line_no is not None:
                where += f":{line_no}"
            where += "]"

        detail = message + where
        if line is not None:
            detail += f"\n  > {line.rstrip()}"
        super().__init__(detail)


class LayerProfileError(OdbError):
    """A layer profile exists but cannot be used as a base polygon.

    Per requirements.md parser constraint 3 a present-but-unparseable layer profile is a hard
    error: silently falling back to the step profile would fabricate geometry
    that does not exist on that layer.
    """

    def __init__(self, layer: str, reason: str) -> None:
        self.layer = layer
        self.reason = reason
        super().__init__(f"layer {layer!r} has an unusable profile: {reason}")


class LayerGeometryError(OdbError):
    """A layer cannot be composed into a result region.

    The document describes one complete board, so a run stops at the first layer
    it cannot compose instead of publishing a document with a hole in it.
    """

    def __init__(self, layer: str, reason: str) -> None:
        self.layer = layer
        self.reason = reason
        super().__init__(f"layer {layer!r} could not be composed: {reason}")
