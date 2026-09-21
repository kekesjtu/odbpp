"""Archive access for ODB++ trees.

An ODB++ "file" is really a directory tree that is shipped as a ``.tgz``,
``.tar.gz``, ``.tar``, ``.zip`` or an already-exploded directory (spec p.21).
On top of that, individual member files may *themselves* be gzip-compressed
("Large File Compression", spec p.31), so decompression is decided by the gzip
magic number rather than by the file name.

Two behaviours here exist to handle real-world data (see
``docs/research/03-cases-inventory-and-observations.md``):

* **Case-insensitive lookup.**  ``matrix/matrix`` declares layer names in upper
  case (``F.CU``) while the on-disk directories are lower case (``f.cu``).  This
  "works" on Windows and fails on Linux, so normalisation is explicit.
* **Path discipline.**  Everything is addressed with ``/``-separated paths
  relative to the job root, never with host-specific separators.
"""

from __future__ import annotations

import gzip
import os
import posixpath
import tarfile
import zipfile
from typing import Optional

from .diagnostics import CASE_INSENSITIVE_COLLISION, MULTIPLE_JOB_ROOTS, Diagnostics
from .errors import ArchiveError

__all__ = ["OdbArchive", "read_maybe_gzipped"]

_GZIP_MAGIC = b"\x1f\x8b"


def read_maybe_gzipped(raw: bytes) -> bytes:
    """Return *raw*, gunzipped if it is a gzip stream.

    Spec p.31 allows ``features``, ``profile``, ``components``, ``netlist``,
    ``eda/data``, ``attrlist`` and ``dcodes`` to be compressed in place.  The
    magic number is authoritative; the name is not.
    """
    if raw[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(raw)
        except OSError as exc:  # truncated or corrupt stream
            raise ArchiveError(f"member is gzip but cannot be decompressed: {exc}") from exc
    return raw


def _normalise(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = posixpath.normpath(p)
    if p == ".":
        return ""
    return p.lstrip("/")


class OdbArchive:
    """Read-only, case-insensitive, gzip-aware view of an ODB++ tree."""

    def __init__(self, path: str, *, diagnostics: Optional[Diagnostics] = None) -> None:
        self.path = path
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics()

        self._kind: str = ""
        self._tar: Optional[tarfile.TarFile] = None
        self._zip: Optional[zipfile.ZipFile] = None

        #: case-folded relative path -> actual relative path
        self._index: dict[str, str] = {}
        #: every actual file path, for fast membership tests
        self._files: set[str] = set()
        #: every directory path (including implicit parents), normalised
        self._dirs: set[str] = {""}

        self._open()

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def open(cls, path: str, *, diagnostics: Optional[Diagnostics] = None) -> "OdbArchive":
        try:
            return cls(path, diagnostics=diagnostics)
        except OSError as exc:
            raise ArchiveError(f"cannot open {path!r}: {exc}") from exc

    def close(self) -> None:
        if self._tar is not None:
            self._tar.close()
            self._tar = None
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "OdbArchive":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"OdbArchive({self.path!r}, kind={self._kind!r}, files={len(self._files)})"

    # ------------------------------------------------------------------ setup

    def _open(self) -> None:
        if os.path.isdir(self.path):
            self._kind = "dir"
            self._scan_dir()
            return

        if not os.path.exists(self.path):
            raise ArchiveError(f"no such file or directory: {self.path}")

        # Decide tar vs zip by content, not by extension: real archives are
        # frequently misnamed (.tgz holding a zip, .zip holding a tar).
        if zipfile.is_zipfile(self.path):
            self._kind = "zip"
            try:
                self._zip = zipfile.ZipFile(self.path)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ArchiveError(f"{self.path!r} is not a readable zip archive: {exc}") from exc
            self._scan_zip()
            return

        try:
            self._tar = tarfile.open(self.path, mode="r:*")
        except tarfile.TarError as exc:
            raise ArchiveError(
                f"{self.path!r} is neither a directory, a zip, nor a readable tar archive: {exc}"
            ) from exc
        self._kind = "tar"
        self._scan_tar()

    def _register(self, relpath: str, *, is_dir: bool) -> None:
        rel = _normalise(relpath)
        if not rel:
            return

        if is_dir:
            self._add_dir(rel)
            return

        self._files.add(rel)
        self._add_dir(posixpath.dirname(rel))

        key = rel.casefold()
        existing = self._index.get(key)
        if existing is not None and existing != rel:
            # Two members differing only by case: keep the first and complain,
            # because which one we pick changes the parse result.
            self.diagnostics.warn(
                CASE_INSENSITIVE_COLLISION,
                f"archive members {existing!r} and {rel!r} differ only by case; using {existing!r}",
                self.path,
            )
            return
        self._index[key] = rel

    def _add_dir(self, rel: str) -> None:
        rel = _normalise(rel)
        while rel and rel not in self._dirs:
            self._dirs.add(rel)
            rel = posixpath.dirname(rel)

    def _scan_dir(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.path):
            rel_dir = _normalise(os.path.relpath(dirpath, self.path))
            if rel_dir:
                self._add_dir(rel_dir)
            # Skip VCS metadata but keep everything else, including dotfiles:
            # `.features.sum` sidecars are legitimate archive members.
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                rel = posixpath.join(rel_dir, name) if rel_dir else name
                self._register(rel, is_dir=False)

    def _scan_tar(self) -> None:
        assert self._tar is not None
        for member in self._tar.getmembers():
            self._register(member.name, is_dir=member.isdir())

    def _scan_zip(self) -> None:
        assert self._zip is not None
        for info in self._zip.infolist():
            self._register(info.filename, is_dir=info.is_dir())

    # --------------------------------------------------------------- lookup

    def resolve(self, relpath: str) -> Optional[str]:
        """Return the actual member path for *relpath*, case-insensitively.

        ``None`` when nothing matches.
        """
        rel = _normalise(relpath)
        if rel in self._files:
            return rel
        return self._index.get(rel.casefold())

    def exists(self, relpath: str) -> bool:
        rel = _normalise(relpath)
        if rel in self._dirs:
            return True
        return self.resolve(rel) is not None

    def is_dir(self, relpath: str) -> bool:
        return _normalise(relpath) in self._dirs

    def list_dir(self, relpath: str = "") -> list[str]:
        """Immediate child names (files and directories) of *relpath*."""
        rel = _normalise(relpath)
        prefix = rel + "/" if rel else ""
        names: set[str] = set()

        for member in self._files:
            if not member.startswith(prefix):
                continue
            rest = member[len(prefix):]
            if "/" in rest:
                names.add(rest.split("/", 1)[0])
            else:
                names.add(rest)

        for d in self._dirs:
            if not d.startswith(prefix):
                continue
            rest = d[len(prefix):]
            if rest and "/" not in rest:
                names.add(rest)

        return sorted(names)

    @property
    def files(self) -> list[str]:
        return sorted(self._files)

    @property
    def dirs(self) -> list[str]:
        return sorted(d for d in self._dirs if d)

    # ----------------------------------------------------------------- read

    def read_bytes(self, relpath: str) -> bytes:
        actual = self.resolve(relpath)
        if actual is None:
            raise ArchiveError(f"no such member: {relpath}")

        if self._kind == "dir":
            with open(os.path.join(self.path, actual.replace("/", os.sep)), "rb") as fh:
                raw = fh.read()
        elif self._kind == "tar":
            assert self._tar is not None
            handle = self._tar.extractfile(actual)
            if handle is None:
                raise ArchiveError(f"member is not a regular file: {relpath}")
            with handle:
                raw = handle.read()
        else:
            assert self._zip is not None
            with self._zip.open(actual) as handle:
                raw = handle.read()

        return read_maybe_gzipped(raw)

    def read_text(self, relpath: str, *, encoding: str = "utf-8") -> str:
        """Read a member as text.

        A leading UTF-8 BOM is dropped: exporters on Windows emit one and it would
        otherwise become part of the first token (``UNITS`` would never match).

        The ``latin-1`` fallback mirrors what CAM tools do in practice; ODB++ is
        ASCII by spec but archived jobs occasionally carry stray high bytes, and
        refusing to read a whole board over one byte is the worse outcome.
        """
        raw = self.read_bytes(relpath)
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return text[1:] if text.startswith("\ufeff") else text

    def read_text_if_exists(self, relpath: str) -> Optional[str]:
        try:
            return self.read_text(relpath)
        except ArchiveError:
            return None

    # ------------------------------------------------------------- job root

    def find_job_roots(self) -> list[str]:
        """Return every directory that looks like an ODB++ job root.

        A job root is a directory containing ``matrix/matrix`` or ``misc/info``.
        Archives in the wild wrap the job in one or more redundant parent
        directories (``odb/``, ``<jobname>/``, or even both), so this searches
        the whole tree rather than assuming a fixed depth.
        """
        candidates: list[str] = []
        for d in sorted(self._dirs):
            if self.exists(posixpath.join(d, "matrix", "matrix")) or \
               self.exists(posixpath.join(d, "misc", "info")):
                candidates.append(d)

        # Drop any candidate nested inside another candidate: the outermost one
        # is the job root and the inner hit is a coincidence.
        roots: list[str] = []
        for cand in candidates:
            prefix = cand + "/" if cand else ""
            if any(other == "" or cand.startswith(other + "/") for other in roots):
                continue
            roots = [r for r in roots if not (prefix and r.startswith(prefix))]
            roots.append(cand)
        return roots

    def require_job_root(self) -> str:
        """Return the single job root, or raise with an actionable message."""
        roots = self.find_job_roots()
        if not roots:
            raise ArchiveError(
                f"no ODB++ job root found in {self.path!r} "
                "(expected a directory containing matrix/matrix or misc/info)"
            )
        if len(roots) > 1:
            self.diagnostics.warn(
                MULTIPLE_JOB_ROOTS,
                f"multiple job roots found ({', '.join(repr(r) for r in roots)}); "
                f"using {roots[0]!r}",
                self.path,
            )
        return roots[0]
