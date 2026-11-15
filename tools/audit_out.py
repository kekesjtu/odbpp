"""Independently audit MaterialMap geometry, statistics, Z and finite properties."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def physical_shape(polygons):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    islands = [Polygon(p['points']) for p in polygons if p['island']]
    holes = {i: [] for i in range(len(islands))}
    for ring in (p for p in polygons if not p['island']):
        hole = Polygon(ring['points'])
        owner = min((i for i, p in enumerate(islands) if p.covers(hole)), key=lambda i: islands[i].area)
        holes[owner].append(ring['points'])
    return unary_union([Polygon(p.exterior.coords, holes[i]) for i, p in enumerate(islands)])


def _components(shape):
    if shape.is_empty:
        return []
    return [shape] if shape.geom_type == 'Polygon' else [p for p in shape.geoms if p.geom_type == 'Polygon']


def _topology(original, model):
    from shapely.geometry import Polygon
    sources, targets = _components(original), _components(model)
    holes = [Polygon(h) for s in sources for h in s.interiors]
    model_holes = [Polygon(h) for t in targets for h in t.interiors]

    def correspondence(left, right):
        forward = [sum(a.intersection(b).area > 1e-10 for b in right) for a in left]
        reverse = [sum(a.intersection(b).area > 1e-10 for a in left) for b in right]
        return {'original_count': len(left), 'model_count': len(right),
                'forward_matches': forward, 'reverse_matches': reverse,
                'preserved': len(left) == len(right) and all(n == 1 for n in forward + reverse)}

    components = correspondence(sources, targets)
    holes = correspondence(holes, model_holes)
    positive = all(len(_components(t.buffer(-1e-7))) == 1 for t in targets)
    return {'components': components, 'holes': holes, 'positive_width_connections': positive,
            'preserved': components['preserved'] and holes['preserved'] and positive}


def audit_outputs(out_dir: Path = ROOT / 'out'):
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pcbeq_rust.materialmap import read_document

    records = []
    for path in sorted(out_dir.rglob('materialmap.json')):
        directory = path.parent
        meta = json.loads((directory / 'diagnostics.json').read_text(encoding='utf-8'))
        assert meta['issues']['status'] != 'failed', f'{directory}: latest run failed'
        materialmap = read_document(path)
        step = json.loads((directory.parent / 'pcbkit/intermediate.json').read_text(encoding='utf-8'))['steps'][0]
        outlines = {d['id']: physical_shape(d['polygons']) for d in step['profiles']}
        assigned = {name:p['id'] for p in step['profiles'] for name in p['layers']}
        layers = {l['name']: l for l in step['layers']}
        profiles = meta['geometry']['profiles']
        assert meta['geometry']['profile_count'] == len(profiles)
        by_layer = {}
        for region in materialmap.regions:
            by_layer.setdefault(region.layer.name, []).append(region)
        reported_layers = []
        for profile_id, profile in profiles.items():
            assert profile['layer_count'] == len(profile['layers'])
            reported_layers.extend(profile['layers'])
            representative = by_layer[profile['layers'][0]]
            shapes = [box(r.bounds_mm.x[0], r.bounds_mm.y[0], r.bounds_mm.x[1], r.bounds_mm.y[1])
                      for r in representative]
            modeled = unary_union(shapes)
            assert abs(sum(p.area for p in shapes) - modeled.area) < 1e-7
            assert profile['region_count'] == len(representative)
            assert profile['cell_count'] == sum(len(r.values) for r in representative)
            for field, grid in (('region_size_mm', False), ('cell_size_mm', True)):
                for axis in 'xy':
                    sizes = [(getattr(r.bounds_mm, axis)[1] - getattr(r.bounds_mm, axis)[0]) /
                             (getattr(r.grid_counts, axis) if grid else 1) for r in representative]
                    assert math.isclose(profile[field]['min'][axis], min(sizes), abs_tol=1e-9)
                    assert math.isclose(profile[field]['max'][axis], max(sizes), abs_tol=1e-9)
            accuracy = meta['accuracy']['profiles'][profile_id]
            area = accuracy['area']
            for name in profile['layers']:
                source = outlines[assigned[name]]
                layout = [(r.bounds_mm.x, r.bounds_mm.y, r.grid_counts) for r in by_layer[name]]
                assert layout == [(r.bounds_mm.x, r.bounds_mm.y, r.grid_counts) for r in representative]
                lost, gained = source.difference(modeled).area, modeled.difference(source).area
                # Native input normalization is 1e-6 mm; independent boolean kernels
                # can also perturb boundary coordinates. Bound area drift by perimeter.
                area_tolerance = max(1e-7, (source.length + modeled.length) * 1e-6)
                for field, actual in (('original_mm2', source.area), ('model_mm2', modeled.area),
                                      ('lost_mm2', lost), ('gained_mm2', gained)):
                    assert math.isclose(area[field], actual, rel_tol=1e-8, abs_tol=area_tolerance), (name, field)
                assert math.isclose(area['relative_error'], (lost + gained) / source.area,
                                    abs_tol=area_tolerance / source.area)
                assert lost + gained <= source.area * area['allowed_relative_error'] + area_tolerance
                assert area['passed'] and accuracy['topology']['preserved']
                topology = _topology(source, modeled)
                assert topology == accuracy['topology']
        assert len(reported_layers) == len(set(reported_layers))
        assert set(reported_layers) == set(by_layer)
        errors = {name: issue for issue in meta['issues']['errors'] for name in issue.get('layers', [])}
        for region in materialmap.regions:
            source = layers[region.layer.name]
            z = source['z_mm'] or {}
            start, thickness = z.get('start_mm'), z.get('thickness_mm')
            measured = start is not None and thickness is not None
            assert region.bounds_mm.z == ((start, start + thickness) if measured else (None, None))
            if not measured:
                issue = errors[region.layer.name]
                assert not issue['blocking'] and issue['code'].startswith('z.')
            assert all(math.isfinite(v) and v > 0 for values in region.values for v in values)
        records.append({'case': directory.relative_to(out_dir).as_posix(),
                        'physical_regions': len(materialmap.regions),
                        'material_rows': sum(len(r.values) for r in materialmap.regions),
                        'max_profile_area_error_ratio': max(p['area']['relative_error']
                                                           for p in meta['accuracy']['profiles'].values())})
        print(records[-1], flush=True)
    assert records, 'no MaterialMap JSON outputs to audit'
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'out')
    parser.add_argument('--output', type=Path, required=True, help='audit report, normally under task-specific tmp/')
    args = parser.parse_args()
    report = {'cases': audit_outputs(args.out)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
