#!/usr/bin/env python
"""Extract the ODB++ specification PDF into a page-tagged plain-text file.

The spec is the authoritative reference for this project but is a 515-page PDF.
This tool produces a greppable text dump with ``===== PAGE <n> =====`` markers so
that page numbers can be cited (and re-checked) directly.

Usage
-----
    python tools/extract_spec_text.py                 # -> build/spec.txt
    python tools/extract_spec_text.py -o /tmp/spec.txt
    python tools/extract_spec_text.py --page 36 41    # print a page range
    python tools/extract_spec_text.py --grep "copper weight"

The generated text is a derived artifact and is intentionally not committed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PDF = os.path.join(REPO_ROOT, "docs", "ODB++_Format_User_Guide.pdf")
DEFAULT_OUT = os.path.join(REPO_ROOT, "build", "spec.txt")

PAGE_MARK = "===== PAGE {} ====="

# The spec contains non-ASCII typography; Windows consoles default to a legacy
# code page (e.g. GBK) and would raise UnicodeEncodeError on print().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - non-standard stream
        pass


def extract(pdf_path: str, out_path: str) -> int:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency hint
        sys.exit("pypdf is required: uv sync --all-extras")

    reader = PdfReader(pdf_path)
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # keep going, record the failure
            text = f"<<extract failed: {exc}>>"
        parts.append(f"\n{PAGE_MARK.format(i)}\n{text}")

    blob = "".join(parts)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(blob)
    print(f"{len(reader.pages)} pages -> {out_path} ({len(blob)} chars)")
    return 0


def load_pages(path: str) -> dict[int, str]:
    if not os.path.exists(path):
        sys.exit(f"missing {path}; run without --page/--grep first")
    text = open(path, encoding="utf-8").read()
    fields = re.split(r"\n===== PAGE (\d+) =====\n", text)
    pages: dict[int, str] = {}
    for i in range(1, len(fields), 2):
        pages[int(fields[i])] = fields[i + 1]
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--pdf", default=DEFAULT_PDF, help="input PDF")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT, help="output text path")
    ap.add_argument("--page", nargs=2, type=int, metavar=("FROM", "TO"),
                    help="print a page range from the generated text")
    ap.add_argument("--grep", metavar="PATTERN",
                    help="print matching lines with their page numbers")
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate the text even when it already exists")
    args = ap.parse_args(argv)

    if args.rebuild or not os.path.exists(args.out):
        extract(args.pdf, args.out)

    if args.page:
        a, b = args.page
        pages = load_pages(args.out)
        for p in range(a, b + 1):
            if p in pages:
                print(f"----- p{p} -----")
                print(pages[p].strip())

    if args.grep:
        rx = re.compile(args.grep, re.I)
        pages = load_pages(args.out)
        for p in sorted(pages):
            for line in pages[p].splitlines():
                if rx.search(line):
                    print(f"p{p}: {line.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
