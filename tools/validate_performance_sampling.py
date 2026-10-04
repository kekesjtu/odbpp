"""Compare raw properties, masks and geometry at three raster resolutions.

Run via uv run --all-extras python tools/validate_performance_sampling.py.
The baseline is saved from Git into build/ without switching the checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--worker', action='store_true')
parser.add_argument('--baseline', action='store_true')
parser.add_argument('--baseline-ref', default='adb64af')
parser.add_argument('--baseline-source-root')
parser.add_argument('--case')
parser.add_argument('--px',type=int)
parser.add_argument('--py',type=int)
parser.add_argument('--output')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]

if args.worker:
    if args.baseline:
        sys.path.insert(0,args.baseline_source_root)
    import numpy as np
    from odbpp.equiv import EquivalenceOptions, equivalence_from_document
    from odbpp.grid import GridOptions
    start = time.perf_counter()
    source = next(p for p in (root/'out'/args.case).glob('*.json') if '.MaterialMap.' not in p.name)
    document = json.loads(source.read_text(encoding='utf-8'))
    masks = {}
    def mask_sink(name,mask,pitch):
        masks[name] = {'sha256':hashlib.sha256(mask.pixels).hexdigest(),'tones':mask.tone_counts()}
    result = equivalence_from_document(document,EquivalenceOptions(
        GridOptions(args.px,args.py,mesh_preset='standard')),mask_sink=mask_sink)
    elapsed = time.perf_counter()-start
    geometry = {'rectification':result.rectification.as_dict(),
                'x_lines':result.grid.x_lines,'y_lines':result.grid.y_lines,
                'cells':[(c.region_id,c.bounds) for c in result.grid.cells]}
    metadata = result.metadata()
    metadata.pop('pipeline_fingerprint')
    values = np.array([[c.kx,c.ky,c.kz,c.density,c.specific_heat]
                       for c in result.cells],dtype=np.float64).reshape(-1,5)
    output = Path(args.output)
    np.save(output.with_suffix('.npy'),values)
    record = {'case':args.case,'raster':[args.px,args.py],'fatal':result.fatal,
              'elapsed_s':elapsed,'material_rows':result.cell_count,
              'geometry_sha256':hashlib.sha256(json.dumps(geometry,sort_keys=True).encode()).hexdigest(),
              'metadata_sha256':hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest(),
              'properties_sha256':hashlib.sha256(values.tobytes()).hexdigest(),
              'masks':masks}
    output.write_text(json.dumps(record,indent=2),encoding='utf-8')
    sys.exit()

import numpy as np
scratch = root/'build/performance-sampling'
scratch.mkdir(exist_ok=True)
from benchmark_pipeline import snapshot_source
baseline_source = snapshot_source(args.baseline_ref,scratch/'source')
report = {'schema':'odbpp/performance-sampling-validation-1',
          'baseline_ref':args.baseline_ref,
          'optimized_ref':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
          'cases':{}}
for case in ('designodb-rigidflex.tgz','kicad-kitchen-sink'):
    meta = json.loads(next((root/'out'/case).glob('*.meta.json')).read_text(encoding='utf-8'))
    px,py = meta['raster']['px'],meta['raster']['py']
    records=[]
    arrays=[]
    for label,counts in (('default',(px,py)),('double',(2*px,2*py)),('insufficient',(64,64))):
        pair=[]
        for baseline in (True,False):
            output = scratch/f'{case}-{label}-{baseline}.json'
            command = [sys.executable,str(Path(__file__).resolve()),'--worker',
                       '--case',case,'--px',str(counts[0]),'--py',str(counts[1]),'--output',str(output)]
            if baseline:
                command.extend(['--baseline','--baseline-source-root',baseline_source])
            subprocess.run(command,cwd=root,check=True)
            pair.append(json.loads(output.read_text(encoding='utf-8')))
            if not baseline:
                arrays.append(np.load(output.with_suffix('.npy')))
        old,new=pair
        for key in ('geometry_sha256','metadata_sha256','properties_sha256','masks','fatal'):
            assert old[key]==new[key],(case,label,key)
        assert new['fatal'] == (label=='insufficient')
        records.append({'label':label,'baseline':old,'optimized':new,'exact_match':True})
        print(case,label,'exact match',flush=True)
    assert len({r['optimized']['geometry_sha256'] for r in records})==1
    relative=np.abs(arrays[0]-arrays[1])/arrays[1]
    report['cases'][case]={'geometry_independent_of_raster':True,'runs':records,
        'relative_error_default_vs_double':{
            name:{'median':float(np.median(relative[:,i])),
                  'p95':float(np.quantile(relative[:,i],.95)),'max':float(relative[:,i].max())}
            for i,name in enumerate(('kx','ky','kz','density','specific_heat'))}}
destination = Path(args.output) if args.output else root/'docs/reviews/performance-sampling-validation.json'
destination.write_text(
    json.dumps(report,indent=2)+'\n',encoding='utf-8')
