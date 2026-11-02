"""Dependency-free PNG encoding for material masks and rendered heat maps.

Supports 8-bit greyscale and RGB, filter type 0 and no interlacing. Heat-map
layout and text are rendered by Matplotlib before its RGB rows are encoded.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable

__all__ = [
    "write_greyscale_png",
    "write_rgb_png",
    "Rgb",
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
