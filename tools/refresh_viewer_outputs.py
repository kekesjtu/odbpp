"""Refresh only inline viewer CSS/JS after an interaction implementation change.

For geometry, markup or metadata changes, use sync_out.py --force instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from odbpp.svg import _CSS, _JS

ROOT = Path(__file__).resolve().parents[1]


def replace_block(text: str, tag: str, value: str) -> str:
    start = text.index(f'<{tag}>') + len(tag) + 2
    end = text.index(f'</{tag}>', start)
    return text[:start] + value + text[end:]


def payload(text: str) -> str:
    return replace_block(replace_block(text, 'style', ''), 'script', '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'out')
    parser.add_argument('--report', type=Path, default=ROOT/'docs/reviews/viewer-output-validation.json')
    args = parser.parse_args()
    results = []
    for path in sorted(args.out.rglob('*.html')):
        original = path.read_text(encoding='utf-8')
        if 'class="odb-svg"' not in original:
            continue
        updated = replace_block(replace_block(original, 'style', _CSS), 'script', _JS)
        assert payload(original) == payload(updated), path
        results.append(dict(path=path.relative_to(args.out).as_posix(),
                            non_code_sha256=hashlib.sha256(payload(original).encode()).hexdigest(),
                            changed=original != updated))
        if original != updated:
            path.write_text(updated, encoding='utf-8', newline='\n')
        assert path.read_text(encoding='utf-8') == updated, path
    if not results:
        parser.error('no standalone ODB++ viewer HTML files found')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(dict(viewers=results,non_code_unchanged=True),
                                      ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(results)} viewers checked; {sum(r["changed"] for r in results)} refreshed')


if __name__ == '__main__':
    main()
