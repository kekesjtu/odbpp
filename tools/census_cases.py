"""Scan ODB++ cases and report feature-record / polarity / symbol-name statistics.

Usage:  python tools/census_cases.py <case_dir_or_tgz> [<case> ...]

Reads *.tgz transparently. Ignores '.sum' sidecars. Prints a per-case summary of:
  * feature record types (P / L / A / T / B / S) with polarity split
  * surface OB polygon types (I island / H hole)
  * symbol name prefixes used in $ declarations
  * custom symbol usage
"""
from __future__ import annotations

import os
import re
import sys
import tarfile
import zipfile
from collections import Counter

REC = re.compile(r"^([PLATBS])\s+(\S.*)$")
OB = re.compile(r"^OB\s+\S+\s+\S+\s+([IH])")


def iter_feature_files(root: str):
    """Yield (label, text) for every features/profile-like file under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if fn.startswith(".") or fn.endswith(".sum"):
                continue
            if fn not in ("features", "profile"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                raw = open(path, "rb").read()
            except OSError:
                continue
            if raw[:2] == b"\x1f\x8b":
                import gzip

                raw = gzip.decompress(raw)
            yield os.path.relpath(path, root), raw.decode("utf-8", "replace")


def iter_tgz(tgz: str):
    with tarfile.open(tgz) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            base = os.path.basename(m.name)
            if base.startswith(".") or base.endswith(".sum"):
                continue
            if base not in ("features", "profile"):
                continue
            fh = tf.extractfile(m)
            if fh is None:
                continue
            raw = fh.read()
            if raw[:2] == b"\x1f\x8b":
                import gzip

                raw = gzip.decompress(raw)
            yield m.name, raw.decode("utf-8", "replace")


def iter_zip(zpath: str):
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            base = os.path.basename(name)
            if base.startswith(".") or base.endswith(".sum"):
                continue
            if base not in ("features", "profile"):
                continue
            raw = zf.read(name)
            if raw[:2] == b"\x1f\x8b":
                import gzip

                raw = gzip.decompress(raw)
            yield name, raw.decode("utf-8", "replace")


def source(path: str):
    if os.path.isdir(path):
        return iter_feature_files(path)
    low = path.lower()
    if low.endswith((".tgz", ".tar.gz", ".tar")):
        return iter_tgz(path)
    if low.endswith(".zip"):
        return iter_zip(path)
    raise SystemExit(f"unsupported input: {path}")


def census(path: str) -> None:
    recs = Counter()
    polarity = Counter()
    poly = Counter()
    prefixes = Counter()
    in_symbols = 0
    files = 0
    layers = set()

    for label, text in source(path):
        files += 1
        norm = label.replace("\\", "/")
        if "/symbols/" in norm:
            in_symbols += 1
        m = re.search(r"/layers/([^/]+)/", norm)
        if m:
            layers.add(m.group(1).lower())

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("$"):
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[1]
                    pre = re.match(r"^([A-Za-z_]+?)(?=[\d.]|$)", name)
                    if pre:
                        key = pre.group(1)
                    elif re.match(r"^[A-Za-z]+[0-9._x]", name):
                        key = "(custom)"
                    else:
                        key = name
                    prefixes[key] += 1
                continue
            mo = REC.match(line)
            if mo:
                kind = mo.group(1)
                if kind == "S":
                    recs["S"] += 1
                    continue
                fields = line.split()
                # polarity is the field right after the geometry payload;
                # walk from the right to find a lone P/N token before ';' or ID=
                pol = None
                for tok in fields[::-1]:
                    if tok in ("P", "N"):
                        pol = tok
                        break
                    if tok.startswith("ID=") or ";" in tok or tok[0].isdigit() or tok in ("Y",):
                        continue
                polarity[f"{kind}:{pol or '?'}"] += 1
                recs[kind] += 1
                continue
            mo = OB.match(line)
            if mo:
                poly[mo.group(1)] += 1

    print(f"### {path}")
    print(f"    feature-like files : {files}  (inside symbols/: {in_symbols})")
    print(f"    layers seen        : {len(layers)}")
    print("    records            : " + ", ".join(f"{k}={v}" for k, v in sorted(recs.items())))
    print("    polarity           : " + ", ".join(f"{k}={v}" for k, v in sorted(polarity.items())))
    print("    OB polygons        : " + ", ".join(f"{k}={v}" for k, v in sorted(poly.items())))
    top = ", ".join(f"{k}={v}" for k, v in prefixes.most_common(14))
    print(f"    symbol prefixes    : {top}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cases")
        args = [os.path.join(here, n) for n in sorted(os.listdir(here))
                if os.path.isdir(os.path.join(here, n))
                or n.lower().endswith((".tgz", ".tar.gz", ".tar", ".zip"))]
    for a in args:
        census(a)
