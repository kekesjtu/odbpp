"""Compare the region JSON pipeline with an isolated former CSV implementation.

uv run --all-extras python tools/validate_materialmap.py --baseline-ref 51c268b
Both consumers use the same physical input. The historical reader's test fixture
reconstructs its former redundant drill Z from endpoint copper layers; this is
isolated audit code, not runtime format migration.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
CASES=("minimal-test-odb","kicad-kitchen-sink","kicad-video-odb.tgz","designodb-rigidflex.tgz")


def digest(value):
    return hashlib.sha256(json.dumps(value,separators=(",",":")).encode()).hexdigest()


def worker(args):
    if args.source_root:
        sys.path.insert(0,str(args.source_root.resolve()))
    from odbpp.equiv import equivalence_from_document, EquivalenceOptions
    from odbpp.grid import GridOptions
    from benchmark_pipeline import peak_memory_bytes
    directory=ROOT/"out"/args.case
    source=next(p for p in directory.glob("*.json") if not p.name.endswith((".MaterialMap.json",".MaterialMap.meta.json")))
    document=json.loads(source.read_text(encoding="utf-8"))
    if args.source_root:
        # The frozen CSV implementation predates removal of DRILL.z_mm. Supply
        # that derived value only to its isolated historical test fixture.
        for step in document["steps"]:
            copper={l["copper_index"]:l for l in step["layers"] if "copper_index" in l}
            for layer in step["layers"]:
                span=layer.get("copper_span")
                if layer["type"]=="DRILL" and span and "z_mm" not in layer:
                    top,bottom=copper[span[0]],copper[span[1]]
                    layer["z_mm"]={"start_mm":top["z_mm"]["start_mm"],
                        "end_mm":bottom["z_mm"]["end_mm"],
                        "thickness_known":bottom["z_mm"]["thickness_known"],
                        "source":f"drill span {top['name']}..{bottom['name']}"}
    folder=args.workspace/args.case;folder.mkdir(parents=True,exist_ok=True)
    cell_hash=hashlib.sha256();masks={};count=0
    def capture(cell):
        nonlocal count
        cell_hash.update(cell.layer_name.encode()+b"\0")
        cell_hash.update(struct.pack("!11d?",cell.x0,cell.y0,cell.x1,cell.y1,
            cell.z_start_mm,cell.z_end_mm,cell.kx,cell.ky,cell.kz,cell.density,cell.specific_heat,
            cell.thickness_known))
        count+=1
    def mask_sink(name,mask,pitch):
        masks[name]={"sha256":hashlib.sha256(mask.pixels).hexdigest(),"pitch_mm":pitch}
    options=EquivalenceOptions(GridOptions(1,1,mesh_preset="standard",auto_raster=True))
    start=time.perf_counter()
    if args.source_root:
        from odbpp.equiv import CSV_COLUMNS
        path=folder/"baseline.csv"
        with path.open("w",encoding="utf-8",newline="") as handle:
            writer=csv.writer(handle);writer.writerow(CSV_COLUMNS)
            def sink(cell):
                capture(cell);writer.writerow(cell.csv_row())
            result=equivalence_from_document(document,options,cell_sink=sink,mask_sink=mask_sink)
    else:
        from odbpp.materialmap import MaterialMapWriter
        path=folder/"current.MaterialMap.json"
        with MaterialMapWriter(path) as writer:
            def sink(cell):
                capture(cell);writer.add(cell)
            result=equivalence_from_document(document,options,cell_sink=sink,mask_sink=mask_sink)
            writer.finish(result)
    elapsed=time.perf_counter()-start
    if result.fatal: raise RuntimeError([d.message for d in result.diagnostics.errors])
    return {"equivalence_and_write_seconds":elapsed,"peak_memory_mib":peak_memory_bytes()/2**20,
        "bytes":path.stat().st_size,"cells":count,"regions":len(result.rectification.rectangles),
        "raw_cells_sha256":cell_hash.hexdigest(),"masks":masks,
        "geometry_sha256":digest(([r.bounds for r in result.rectification.rectangles],
                                  result.grid.x_lines,result.grid.y_lines))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref",default="51c268b")
    parser.add_argument("--case",choices=CASES)
    parser.add_argument("--worker",action="store_true")
    parser.add_argument("--source-root",type=Path)
    parser.add_argument("--workspace",type=Path,default=ROOT/"build/materialmap-process-validation")
    parser.add_argument("--output",type=Path,default=ROOT/"docs/equivalence/materialmap-process-validation.json")
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args)));return
    from benchmark_pipeline import snapshot_source
    baseline=snapshot_source(args.baseline_ref,args.workspace/"baseline-source")
    report={"baseline_ref":args.baseline_ref,"measurement":"isolated sequential workers; same intermediate; single observation with other local work possible", "cases":{}}
    for case in ([args.case] if args.case else CASES):
        records={}
        for label,source in (("baseline",baseline),("current",None)):
            command=[sys.executable,str(Path(__file__).resolve()),"--worker","--case",case,
                     "--workspace",str(args.workspace/label)]
            if source:command += ["--source-root",source]
            done=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding="utf-8",check=True)
            records[label]=json.loads(done.stdout)
        checks={k:records["baseline"][k]==records["current"][k]
                for k in ("raw_cells_sha256","geometry_sha256","masks","cells","regions")}
        if not all(checks.values()):raise RuntimeError((case,checks))
        records["checks"]=checks;report["cases"][case]=records
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(f"{case}: raw cells, masks and geometry identical; {records['baseline']['bytes']} -> {records['current']['bytes']} bytes",flush=True)


if __name__=="__main__":main()
