"""Synchronise case outputs through the independent CLI process pipeline."""
from __future__ import annotations
import argparse
import hashlib
from html import escape
import json
import math
from pathlib import Path
import shutil
try:
    from tools.run_pipeline import execute, forwarded
except ModuleNotFoundError:
    from run_pipeline import execute, forwarded
ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT
CASES = ROOT / 'cases'
OUT = ROOT / 'out'

def case_inputs():
    found = []
    for path in sorted(CASES.iterdir()):
        if path.is_dir() and next(path.rglob('misc/info'), None) is not None:
            found.append((path.name, path))
        elif path.is_file() and path.suffix.lower() in {'.zip', '.tgz'}:
            found.append((path.name, path))
    for variant in ('native', 'clean'):
        found += [(f'kicad-positive/{variant}/{p.stem}', p) for p in sorted((CASES / 'kicad-positive' / variant).glob('*.zip'))]
    if not found:
        raise RuntimeError('no ODB++ cases found')
    return found

def fingerprint(package):
    digest = hashlib.sha256()
    root = SOURCE_ROOT / 'packages' / package
    sources = root / 'python' / package.replace('-', '_')
    paths = list(sources.rglob('*.py'))
    paths += list((root / 'src').rglob('*.rs'))
    paths += list((SOURCE_ROOT / 'crates').rglob('*.rs'))
    paths += [root / 'Cargo.toml', SOURCE_ROOT / 'Cargo.toml', SOURCE_ROOT / 'Cargo.lock']
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()

def source_mtime(source):
    return max((p.stat().st_mtime_ns for p in source.rglob('*') if p.is_file()), default=source.stat().st_mtime_ns) if source.is_dir() else source.stat().st_mtime_ns

def sizes(value, default):
    result = [default, default] if value is None else [value, value] if isinstance(value, (int, float)) else list(value)
    if len(result) == 1:
        result *= 2
    if len(result) != 2 or any((not math.isfinite(v) or v <= 0 for v in result)):
        raise ValueError('provide one or two finite positive cell sizes')
    return result

def existing_preview(directory, source, *, backend='rust', per_layer_png=False):
    path = directory / 'pcbkit/intermediate.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        cache = json.loads((directory / 'pipeline/cache.json').read_text(encoding='utf-8'))
        if Path(data['path']).resolve() != source.resolve() or len(data['steps']) != 1:
            return None
        if cache['parser'] != fingerprint('pcbkit-rust') or cache['source_mtime_ns'] != source_mtime(source):
            return None
        if not all(((directory / 'pcbkit' / p).is_file() for p in ('preview.svg', 'preview.html', 'diagnostics/run.json'))):
            return None
        if per_layer_png:
            base = directory / 'pcbkit'
            owned = json.loads((base / 'diagnostics/artifacts.json').read_text(encoding='utf-8'))['files']
            pngs = [name for name in owned if name.startswith('layers/') and name.endswith('.png')]
            # Additional MISC images belong to the manifest, not the BOARD JSON catalogue.
            expected = len(data['steps'][0]['layers'])
            if len(pngs) < expected or any(not (base / name).is_file() for name in pngs):
                return None
        return (path, data)
    except (OSError, ValueError, KeyError, TypeError):
        return None

def existing_equivalence(directory, preview, *, px, py, max_cell_mm=5, grid_level=None, mode=1, draw_grid=False, max_area_error_ratio=0.01, backend='rust'):
    try:
        cache = json.loads((directory / 'pipeline/cache.json').read_text(encoding='utf-8'))
        requested = {'px': px, 'py': py, 'max': sizes(max_cell_mm, 5), 'grid_level': grid_level, 'mode': mode, 'draw_grid': draw_grid, 'max_area_error_ratio': max_area_error_ratio}
        if cache['equivalent'] != fingerprint('pcbeq-rust') or cache['requested'] != requested:
            return None
        if cache['json_sha256'] != hashlib.sha256(preview.read_bytes()).hexdigest():
            return None
        base = directory / 'pcbeq'
        diagnostics = json.loads((base / 'diagnostics.json').read_text(encoding='utf-8'))
        if diagnostics['issues']['status'] == 'failed':
            return None
        owned = ['materialmap.json', 'diagnostics.json', 'index.html', 'regions/regions.png',
                 'regions/regions.svg', 'grids/cartesian-grid.png', 'grids/cartesian-grid.svg']
        from pcbeq_rust.materialmap import read_document
        from pcbeq_rust.heatmap_data import unique_image_stem
        names = dict.fromkeys(r.layer.name for r in read_document(base / 'materialmap.json').regions)
        used = set()
        owned += [f'heatmaps/{unique_image_stem(name, used)}.png' for name in names]
        if any((not (base / p).is_file() or not (base / p).stat().st_size for p in owned)):
            return None
        return base / 'materialmap.json'
    except (OSError, ValueError, KeyError, TypeError):
        return None

def render_index(rows):
    lines = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PCB 案例输出</title>', '<h1>PCB 案例输出</h1><table><tr><th>Case</th><th>产物</th><th>网格</th></tr>']
    links = [('pcbkit/preview.html', '预览'), ('pcbkit/intermediate.json', 'JSON'), ('pcbeq/materialmap.json', 'MaterialMap'), ('pcbeq/diagnostics.json', '诊断'), ('pcbeq/index.html', '图片'), ('pcbeq/regions/regions.svg', 'regions'), ('pcbeq/grids/cartesian-grid.svg', 'grids')]
    for name, source, directory, data, report in rows:
        relative = escape(directory.relative_to(OUT).as_posix())
        items = ' · '.join((f'<a href="{relative}/{p}">{label}</a>' for p, label in links))
        lines.append(f"<tr><td>{escape(name)}</td><td>{items}</td><td>{len(report['regions'])} regions / {sum(len(r['values']) for r in report['regions'])} cells</td></tr>")
    return '\n'.join(lines + ['</table></html>'])

def published_rows():
    """Index published outputs of this product line."""
    rows = []
    paths = sorted(OUT.glob('**/rust/pcbeq/diagnostics.json'))
    for path in paths:
        directory = path.parents[1]
        if json.loads(path.read_text(encoding='utf-8'))['issues']['status'] == 'failed':
            continue
        if not (directory / 'pcbeq/materialmap.json').is_file():
            continue
        report = json.loads((directory / 'pcbeq/materialmap.json').read_text(encoding='utf-8'))
        rows.append((directory.relative_to(OUT).as_posix(), None, directory, None, report))
    return rows

def prune_unmatched(inputs):
    root = OUT.resolve()
    expected = {name for name, _ in inputs}
    keep = {name.split('/')[0] for name in expected} | {'index.html'}
    candidates = [p for p in OUT.iterdir() if p.name not in keep]
    for variant in ('native', 'clean'):
        folder = OUT / 'kicad-positive' / variant
        if folder.is_dir():
            candidates += [p for p in folder.iterdir() if p.relative_to(OUT).as_posix() not in expected]
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or path.resolve() == root:
            raise ValueError(f'unsafe cleanup: {path}')
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('rust',), default='rust')
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--case', metavar='NAME', help='synchronise only the named case (default: all cases)')
    scope.add_argument('--prune', action='store_true', help='remove outputs without a matching case after full sync')
    for flag in ('force', 'force-equivalent', 'heatmap-grid', 'per-layer-png'):
        parser.add_argument('--' + flag, action='store_true')
    parser.add_argument('--px', type=int)
    parser.add_argument('--py', type=int)
    parser.add_argument('--grid-level', choices=('coarse', 'standard', 'fine'))
    parser.add_argument('--mode', type=int, choices=(1, 2), default=1, help='1 = 2DVTN (default), 2 = SPTRA')
    parser.add_argument('--max-cell-mm', nargs='+', type=float)
    parser.add_argument('--max-area-error-ratio', type=float, default=0.01)
    args = parser.parse_args(argv)
    if (args.px is None) != (args.py is None) or (args.px is not None and min(args.px, args.py) <= 0):
        parser.error('provide positive px/py together')
    expert = args.max_cell_mm is not None
    if expert and args.grid_level:
        parser.error('grid level cannot be combined with expert sizes')
    try:
        upper = sizes(args.max_cell_mm, 5)
    except ValueError as exc:
        parser.error(str(exc))
    args.grid_level = args.grid_level or (None if expert else 'standard')
    parser_options, options = forwarded(args)
    inputs = case_inputs()
    if args.case is not None:
        selected = [entry for entry in inputs if entry[0] == args.case]
        if not selected:
            available = ', '.join(name for name, _ in inputs)
            parser.error(f'unknown case {args.case!r}; available cases: {available}')
        inputs = selected
    for name, source in inputs:
        output = OUT / name / args.backend
        valid = None if args.force else existing_preview(output, source, backend=args.backend,
                                                         per_layer_png=args.per_layer_png)
        equivalent = None if valid is None or args.force_equivalent else existing_equivalence(output, valid[0], px=args.px, py=args.py, max_cell_mm=upper, grid_level=args.grid_level, mode=args.mode, draw_grid=args.heatmap_grid, max_area_error_ratio=args.max_area_error_ratio, backend=args.backend)
        result = execute(source, output, parser_args=parser_options, equivalent_args=options, reuse_parser=valid is not None, reuse_equivalent=equivalent is not None, pcbkit_exe='pcbkit-rust', pcbeq_exe='pcbeq-rust')
        if result['status'] != 'success':
            raise RuntimeError(f'pipeline failed for {name}: {result}')
        preview = output / 'pcbkit/intermediate.json'
        cache = {'parser': fingerprint('pcbkit-rust'), 'equivalent': fingerprint('pcbeq-rust'), 'source_mtime_ns': source_mtime(source), 'json_sha256': hashlib.sha256(preview.read_bytes()).hexdigest(), 'requested': {'px': args.px, 'py': args.py, 'max': upper, 'grid_level': args.grid_level, 'mode': args.mode, 'draw_grid': args.heatmap_grid, 'max_area_error_ratio': args.max_area_error_ratio}}
        (output / 'pipeline/cache.json').write_text(json.dumps(cache, indent=2), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    if args.prune:
        prune_unmatched(inputs)
    rows = published_rows()
    (OUT / 'index.html').write_text(render_index(rows), encoding='utf-8')
    print(f"index: {len(rows)} cases -> {OUT / 'index.html'}")
if __name__ == '__main__':
    main()
