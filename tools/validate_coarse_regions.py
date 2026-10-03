"""Compare geometry against a Git baseline and verify new sampling convergence.

uv run --all-extras python tools/validate_coarse_regions.py --baseline-ref 9559b86
Each measurement runs in a separate process; scratch files stay in build/.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "build" / "coarse-region-validation"


def worker(args):
    if args.baseline:
        sys.path.insert(0, str(SCRATCH / "baseline" / "src"))
    from odbpp.equiv import EquivalenceOptions, plan_geometry, equivalence_from_document
    from odbpp.grid import GridOptions, build_grid
    from odbpp.mesh import resolve_mesh
    from odbpp.diagnostics import Diagnostics
    from validate_architecture import peak_memory_mb
    document = json.loads(args.document.read_text(encoding="utf-8"))
    options = EquivalenceOptions(GridOptions(args.px or 4096, args.py or 4096,
        mesh_preset=args.preset, auto_raster=args.px is None))
    diagnostics = Diagnostics()
    start = time.perf_counter()
    if args.sample:
        result = equivalence_from_document(document, options, diagnostics=diagnostics)
        r, grid = result.rectification, result.grid
        import numpy as np
        properties = np.array([[c.kx,c.ky,c.kz,c.density,c.specific_heat]
                               for c in result.cells]).reshape(-1,5)
        assert not result.fatal, [d.message for d in diagnostics.errors]
        assert np.isfinite(properties).all() and (properties > 0).all()
        np.save(args.output.with_suffix('.npy'), properties)
    else:
        r, _ = plan_geometry(document['steps'][0], options, diagnostics)
        mesh, _ = resolve_mesh(document['steps'][0], r.rectangles, options.grid)
        grid = build_grid(r.rectangles, mesh, diagnostics=diagnostics)
    seconds = time.perf_counter() - start
    assert r.ok and not diagnostics.errors, [d.message for d in diagnostics.errors]
    geometry = ([r.bounds for r in r.rectangles], [(c.region_id,c.bounds) for c in grid.cells])
    record = dict(regions=len(r.rectangles), forced_x=len(grid.forced_x), forced_y=len(grid.forced_y),
        grid_cells=len(grid.cells), nx=grid.nx, ny=grid.ny, loss_ratio=r.relative_error,
        seconds=seconds, peak_process_memory_mb=peak_memory_mb(),
        geometry_sha256=hashlib.sha256(json.dumps(geometry).encode()).hexdigest(),
        domains={k:dict(loss_ratio=v['shared_loss_area_mm2']/v['original_area_mm2'],
                       topology_preserved=v['shared_topology']['preserved'])
                 for k,v in r.audit.get('domains',{}).items()})
    if args.sample:
        record.update(raster=[result.raster_px,result.raster_py], material_rows=len(result.cells))
    args.output.write_text(json.dumps(record,indent=2),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref',default='9559b86')
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--sample',action='store_true')
    parser.add_argument('--preset',default='standard',choices=['coarse','standard','fine'])
    parser.add_argument('--document',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--px',type=int)
    parser.add_argument('--py',type=int)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    SCRATCH.mkdir(parents=True,exist_ok=True)
    paths=subprocess.check_output(['git','ls-tree','-r','--name-only',args.baseline_ref,'src/odbpp'],cwd=ROOT).decode().splitlines()
    for name in paths:
        target=SCRATCH/'baseline'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(subprocess.check_output(['git','show',f'{args.baseline_ref}:{name}'],cwd=ROOT))
    report={'baseline_ref':args.baseline_ref,'cases':{}}
    for case in ('designodb-rigidflex.tgz','kicad-kitchen-sink','kicad-video-odb.tgz'):
        document=next(p for p in (ROOT/'out'/case).glob('*.json') if '.MaterialMap.' not in p.name)
        runs={}
        def measure(label, extra):
            output=SCRATCH/f'{case}-{label}.json'
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',
                '--document',str(document),'--output',str(output),*extra],cwd=ROOT,check=True)
            value=json.loads(output.read_text(encoding='utf-8'))
            print(case,label,value,flush=True)
            return value, output
        runs['before']=measure('before',['--baseline'])[0]
        runs['after']=measure('after',[])[0]
        runs['presets']={level:measure(level,['--preset',level])[0] for level in ('coarse','standard','fine')}
        first, first_path=measure('automatic',['--sample'])
        px,py=first['raster']
        second, second_path=measure('double',['--sample','--px',str(px*2),'--py',str(py*2)])
        assert first['geometry_sha256']==second['geometry_sha256'], 'raster changed geometry'
        import numpy as np
        a,b=np.load(first_path.with_suffix('.npy')),np.load(second_path.with_suffix('.npy'))
        error=np.abs(a-b)/b
        runs['sampling']={'geometry_invariant':True,'automatic':first,'double':second,
            'relative_error_vs_double':{name:{'median':float(np.median(error[:,i])),
                'p95':float(np.quantile(error[:,i],.95)),'max':float(error[:,i].max())}
                for i,name in enumerate(('kx','ky','kz','density','specific_heat'))}}
        report['cases'][case]=runs
    destination=args.output or ROOT/'docs/equivalence/coarse-region-validation.json'
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
