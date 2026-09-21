#!/usr/bin/env python
"""Fetch the official ODB++Design Format Specification PDF into docs/.

The spec is the authoritative reference for this project. It is deliberately
not committed (7.9 MB binary, vendor copyright); this script makes it
reproducible instead.

Usage
-----
    python tools/fetch_spec.py            # download if missing, then verify
    python tools/fetch_spec.py --force    # re-download even if present
    python tools/fetch_spec.py --verify   # verify the local file only

Exit codes
----------
    0  local file matches the expected digest
    1  file missing, download failed, or digest mismatch (spec likely updated)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(REPO_ROOT, "docs", "ODB++_Format_User_Guide.pdf")

URL = "https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf"

# Release 8.1 Update 4, Siemens, 2024-07-16, 515 pages, product.version.8.1.4
EXPECTED_MD5 = "f57b7264488e8670f81c6b94cb3b9fe1"
EXPECTED_PAGES = 515


def md5_of(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: str = TARGET) -> bool:
    if not os.path.exists(path):
        print(f"missing: {path}")
        print("run:  python tools/fetch_spec.py")
        return False

    actual = md5_of(path)
    size = os.path.getsize(path)
    if actual != EXPECTED_MD5:
        print(f"MD5 MISMATCH for {path}")
        print(f"  expected {EXPECTED_MD5}")
        print(f"  actual   {actual}  ({size} bytes)")
        print()
        print("The published spec has probably been updated. Before continuing:")
        print("  1. diff the new revision against docs/research/01..04")
        print("  2. update EXPECTED_MD5 / EXPECTED_PAGES in this script")
        print("  3. update docs/README.md and the page citations in the research docs")
        return False

    pages = "?"
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = str(len(reader.pages))
        meta = reader.metadata or {}
        print(f"  title   : {meta.get('/Title')}")
        print(f"  version : {meta.get('/Software Release ')}")
        print(f"  revised : {meta.get('/Revised')}")
    except ImportError:
        print("  (install pypdf for page-count verification: pip install pypdf)")

    if pages != "?" and pages != str(EXPECTED_PAGES):
        print(f"PAGE COUNT MISMATCH: expected {EXPECTED_PAGES}, got {pages}")
        return False

    print(f"OK  {path}")
    print(f"    md5 {actual}, {pages} pages, {size} bytes")
    return True


def download(url: str = URL, dest: str = TARGET) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    print(f"downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if total:
                    print(f"\r  {done * 100 // total:3d}%  {done / 1e6:.1f}/{total / 1e6:.1f} MB",
                          end="", flush=True)
        print()
    except (urllib.error.URLError, OSError) as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit(f"download failed: {exc}")
    os.replace(tmp, dest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=URL, help="override the download URL")
    ap.add_argument("--dest", default=TARGET, help="override the output path")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--verify", action="store_true", help="verify only, never download")
    args = ap.parse_args(argv)

    if args.verify:
        return 0 if verify(args.dest) else 1

    if args.force or not os.path.exists(args.dest):
        download(args.url, args.dest)

    return 0 if verify(args.dest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
