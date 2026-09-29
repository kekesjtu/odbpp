"""Minimal, dependency-free PNG output plus a tiny RGB canvas.

The visualisation stage (data-flow step 5, ``docs/equivalence``) needs exactly
two kinds of image: an 8-bit greyscale material mask and an 8-bit RGB heat map.
Pillow would write both in a few lines, but it is a large compiled dependency
for a project whose runtime requirement is otherwise a single geometry library;
``zlib`` and ``struct`` from the standard library are enough, and an encoder
this short cannot change an image because a library version moved.

Supported and nothing else: colour type 0 (greyscale) and 2 (RGB), bit depth 8,
filter type 0, no interlacing.  Anything else raises.

The canvas exists so the heat map can be composed without a drawing library:
the primitives are the ones actually used -- axis-aligned rectangles, single
pixel lines, whole RGB rows, and text drawn from the compact glyph table below.
The glyph table goes down to lowercase letters and the punctuation that appears
in labels ("W/(m.K)", "n/a", numbers in scientific notation); the layer *name*
is deliberately not drawn -- an arbitrary ODB++ layer name needs a full font,
and the name is already in the file name and the gallery HTML.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable, Iterator, Optional, Sequence

__all__ = [
    "write_greyscale_png",
    "write_rgb_png",
    "Canvas",
    "Rgb",
    "draw_text",
    "text_width",
    "FONT_WIDTH",
    "FONT_HEIGHT",
]


Rgb = tuple[int, int, int]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOUR_GREYSCALE = 0
_COLOUR_RGB = 2

#: IDAT payload size.  Chunking keeps the resident compressed buffer small on a
#: multi-megapixel panel without changing the stream.
_IDAT_CHUNK = 1 << 16


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _write_png(path: str, width: int, height: int, colour_type: int,
               rows: Iterable[bytes], row_bytes: int) -> None:
    """Write an 8-bit PNG from top-down *rows*, each exactly *row_bytes* long."""
    if width < 1 or height < 1:
        raise ValueError(f"image size must be positive, got {width}x{height}")
    written = 0
    with open(path, "wb") as handle:
        handle.write(_SIGNATURE)
        handle.write(_chunk(b"IHDR", struct.pack(
            ">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)))
        compressor = zlib.compressobj(6)
        pending = bytearray()
        for row in rows:
            raw = bytes(row)
            if len(raw) != row_bytes:
                raise ValueError(
                    f"row {written} is {len(raw)} bytes, expected {row_bytes}")
            pending += compressor.compress(b"\x00" + raw)
            while len(pending) >= _IDAT_CHUNK:
                handle.write(_chunk(b"IDAT", bytes(pending[:_IDAT_CHUNK])))
                del pending[:_IDAT_CHUNK]
            written += 1
        if written != height:
            raise ValueError(
                f"got {written} row(s) for a {height}-pixel-high image")
        pending += compressor.flush()
        for start in range(0, len(pending), _IDAT_CHUNK):
            handle.write(_chunk(b"IDAT", bytes(pending[start:start + _IDAT_CHUNK])))
        handle.write(_chunk(b"IEND", b""))


def write_greyscale_png(path: str, width: int, height: int,
                        rows: Iterable[bytes]) -> None:
    """Write a one-byte-per-pixel greyscale PNG, top row first."""
    _write_png(path, width, height, _COLOUR_GREYSCALE, rows, width)


def write_rgb_png(path: str, width: int, height: int,
                  rows: Iterable[bytes]) -> None:
    """Write a three-byte-per-pixel RGB PNG, top row first."""
    _write_png(path, width, height, _COLOUR_RGB, rows, width * 3)


def _clamp(value: int, low: int, high: int) -> int:
    return low if value < low else (high if value > high else value)


class Canvas:
    """A mutable 24-bit RGB raster with the primitives the heat map needs.

    Coordinates are half-open (``x0 <= x < x1``) so adjacent rectangles tile
    exactly.  Nothing is anti-aliased: a heat map is a picture of a discrete
    grid, and blending would invent values between cells.
    """

    def __init__(self, width: int, height: int,
                 background: Rgb = (255, 255, 255)) -> None:
        if width < 1 or height < 1:
            raise ValueError(f"canvas size must be positive, got {width}x{height}")
        self.width = int(width)
        self.height = int(height)
        self.pixels = bytearray(bytes(background) * (self.width * self.height))

    def fill(self, rgb: Rgb) -> None:
        self.pixels[:] = bytes(rgb) * (self.width * self.height)

    def rect(self, x0: int, y0: int, x1: int, y1: int, rgb: Rgb) -> None:
        x0 = _clamp(int(x0), 0, self.width)
        x1 = _clamp(int(x1), 0, self.width)
        y0 = _clamp(int(y0), 0, self.height)
        y1 = _clamp(int(y1), 0, self.height)
        if x1 <= x0 or y1 <= y0:
            return
        run = bytes(rgb) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * self.width + x0) * 3
            self.pixels[start:start + 3 * (x1 - x0)] = run

    def frame(self, x0: int, y0: int, x1: int, y1: int, rgb: Rgb) -> None:
        """One-pixel outline of the half-open rectangle ``[x0,x1) x [y0,y1)``."""
        self.rect(x0, y0, x1, y0 + 1, rgb)
        self.rect(x0, y1 - 1, x1, y1, rgb)
        self.rect(x0, y0, x0 + 1, y1, rgb)
        self.rect(x1 - 1, y0, x1, y1, rgb)

    def blit_row(self, y: int, x: int, row: bytes) -> None:
        """Copy a pre-rendered RGB row at ``(x, y)``, clipped to the canvas."""
        if y < 0 or y >= self.height:
            return
        size = len(row) // 3
        x0 = _clamp(int(x), 0, self.width)
        x1 = _clamp(int(x) + size, 0, self.width)
        if x1 <= x0:
            return
        skip = (x0 - int(x)) * 3
        start = (y * self.width + x0) * 3
        self.pixels[start:start + (x1 - x0) * 3] = row[skip:skip + (x1 - x0) * 3]

    def save(self, path: str) -> None:
        def rows() -> Iterator[bytes]:
            stride = self.width * 3
            for y in range(self.height):
                yield bytes(self.pixels[y * stride:(y + 1) * stride])

        write_rgb_png(path, self.width, self.height, rows())


# ---------------------------------------------------------------------------
# 5x7 bitmap font
#
# One glyph per entry, seven rows of five cells, "1" = ink.  A real font file or
# a text-shaping library would be absurd here; these are the characters that
# actually appear in the labels this module draws.
# ---------------------------------------------------------------------------

FONT_WIDTH = 5
FONT_HEIGHT = 7
#: Horizontal advance: glyph plus a one-column gap.
FONT_ADVANCE = FONT_WIDTH + 1

_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "a": ("00000", "00000", "01110", "00001", "01111", "10001", "01111"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01110"),
    "k": ("10000", "10000", "10010", "10100", "11000", "10100", "10010"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10101", "10101"),
    "n": ("00000", "00000", "10110", "11001", "10001", "10001", "10001"),
    "x": ("00000", "10001", "01010", "00100", "01010", "10001", "10001"),
    "y": ("00000", "10001", "10001", "10001", "01111", "00001", "01110"),
    "z": ("00000", "11111", "00010", "00100", "01000", "10000", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "=": ("00000", "00000", "11111", "00000", "11111", "00000", "00000"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    "?": ("01110", "10001", "00001", "00110", "00100", "00000", "00100"),
}

#: Drawn for any character without a glyph, so a label never silently loses a
#: character and quietly change its meaning.
_TOFU = ("11111", "10001", "10001", "10001", "10001", "10001", "11111")


def text_width(text: str, scale: int = 1) -> int:
    """Pixel width of *text* at integer *scale* (no trailing gap)."""
    if not text:
        return 0
    if scale < 1:
        raise ValueError(f"text scale must be positive, got {scale}")
    return len(text) * FONT_ADVANCE * scale - scale


def draw_text(canvas: Canvas, x: int, y: int, text: str, rgb: Rgb,
              scale: int = 1) -> None:
    """Draw *text* with the top-left corner at ``(x, y)``."""
    if scale < 1:
        raise ValueError(f"text scale must be positive, got {scale}")
    cursor = int(x)
    for char in text:
        glyph = _GLYPHS.get(char, _TOFU)
        for row, cells in enumerate(glyph):
            for column, cell in enumerate(cells):
                if cell == "1":
                    canvas.rect(cursor + column * scale, int(y) + row * scale,
                                cursor + (column + 1) * scale,
                                int(y) + (row + 1) * scale, rgb)
        cursor += FONT_ADVANCE * scale
