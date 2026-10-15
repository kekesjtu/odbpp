"""Reproduce geometry invariants, sampling convergence and isolated process memory.

uv run --all-extras python tools/validate_architecture.py
Outputs docs/equivalence/architecture-validation.json; scratch stays in build/.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def peak_memory_mb():
    if sys.platform != "win32":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize / 1024**2


def worker(args):
    if args.baseline:
        sys.path.insert(0, str(ROOT / "build" / "architecture-baseline" / "src"))
    import numpy as np
    from odbpp.equiv import EquivalenceOptions, equivalence_from_document
    from odbpp.grid import GridOptions
    start = time.perf_counter()
    document = json.loads(args.document.read_text(encoding="utf-8"))
    loaded = time.perf_counter()
    options=(GridOptions(args.px,args.py) if args.baseline else
             GridOptions(args.px,args.py,mesh_preset=args.grid_level,auto_raster=args.auto_raster))
    result = equivalence_from_document(document, EquivalenceOptions(options))
    finished = time.perf_counter()
    geometry = {
        "regions": [r.bounds for r in result.rectification.rectangles],
        "cells": [(c.region_id, c.bounds) for c in result.grid.cells],
    }
    properties = np.array([[c.kx,c.ky,c.kz,c.density,c.specific_heat]
                           for c in result.cells],dtype=float).reshape(-1,5)
    if not args.baseline and not result.fatal:
        from shapely.geometry import box
        from odbpp.equiv import _region_from_polygons
        from odbpp.rectify import profile_shape
        step=document["steps"][0]
        domains={d["id"]:profile_shape(_region_from_polygons(d["polygons"])) for d in step["domains"]}
        layer_domains={l["name"]:l.get("domain_id") for l in step["layers"]}
        low,high=result.grid.size_constraints_mm["min"],result.grid.size_constraints_mm["max"]
        for cell in result.cells:
            shape=box(cell.x0,cell.y0,cell.x1,cell.y1)
            domain=domains[layer_domains[cell.layer_name]]
            assert cell.x0==result.grid.x_lines[cell.i]
            assert cell.x1==result.grid.x_lines[cell.i+1]
            assert cell.y0==result.grid.y_lines[cell.j]
            assert cell.y1==result.grid.y_lines[cell.j+1]
            assert shape.difference(domain).area < 1e-8
            assert shape.difference(domains["step_profile"]).area < 1e-8
            assert box(*result.rectification.rectangles[cell.region_id].bounds).covers(shape)
            assert low[0]-1e-8 <= cell.x1-cell.x0 <= high[0]+1e-8
            assert low[1]-1e-8 <= cell.y1-cell.y0 <= high[1]+1e-8
            assert cell.domain_coverage == 1 and not cell.partial_domain
            assert cell.domain_area_mm2 == cell.cell_area_mm2
            assert cell.z_end_mm >= cell.z_start_mm
        assert np.isfinite(properties).all() and (properties > 0).all()
        for column,(lo,hi) in enumerate([(0.3,385)]*3+[(1850,8960),(385,1100)]):
            assert (properties[:,column]>=lo-1e-9).all() and (properties[:,column]<=hi+1e-9).all()
    shorts=[min(r.width,r.height) for r in result.rectification.rectangles]
    record = {
        "raster":[result.raster_px,result.raster_py],
        "mesh_configuration":getattr(result.grid,"mesh_configuration",None),
        "nx":result.grid.nx,"ny":result.grid.ny,
        "minimum_samples_per_axis":min((min(c.pixel_rows,c.pixel_cols) for c in result.cells),default=0), "baseline":args.baseline, "fatal":result.fatal,
        "json_load_seconds":loaded-start, "equivalence_seconds":finished-loaded,
        "peak_process_memory_mb":peak_memory_mb(),
        "region_count":len(shorts), "short_edge_below_1mm":sum(v<1 for v in shorts),
        "minimum_short_edge_mm":min(shorts),
        "geometric_loss_mm2":result.rectification.lost_area_mm2,
        "geometric_gain_mm2":result.rectification.gained_area_mm2,
        "relative_geometric_error":result.rectification.relative_error,
        "grid_cells":len(result.grid.cells),"material_rows":len(result.cells),
        "geometry_sha256":hashlib.sha256(json.dumps(geometry).encode()).hexdigest(),
        "diagnostic_errors":[d.message for d in result.diagnostics.errors][:4],
    }
    args.output.write_text(json.dumps(record,indent=2),encoding="utf-8")
    np.save(args.output.with_suffix(".npy"),properties)


def baseline_checkout(ref):
    folder=ROOT / "build" / "architecture-baseline"
    folder.mkdir(parents=True,exist_ok=True)
    files=subprocess.check_output(["git","ls-tree","-r","--name-only",ref,"src/odbpp"],cwd=ROOT).decode().splitlines()
    for name in files:
        target=folder/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(subprocess.check_output(["git","show",f"{ref}:{name}"],cwd=ROOT))


def audit_outputs():
    """Reconstruct row-major JSON cells and independently audit vector/sampling invariants."""
    import math
    from shapely.geometry import box
    from odbpp.equiv import _region_from_polygons, pipeline_fingerprint
    from odbpp.rectify import profile_shape
    from odbpp.materialmap import read_document, iter_cells
    from odbpp.raster import RasterAxis
    records=[]
    for path in sorted((ROOT/"out").rglob("*.MaterialMap.json")):
        directory=path.parent
        meta=json.loads((directory/"diagnostics/run.json").read_text(encoding="utf-8"))
        materialmap=read_document(path)
        assert meta["pipeline_fingerprint"]==pipeline_fingerprint()
        assert meta["drill_model"]=="solid_copper" and meta["drill_scope"]=="dielectric_only"
        assert all(not l["drill_overlays"] for l in meta["layers"] if l["type"]!="DIELECTRIC")
        assert not any(d["level"]=="error" for d in meta["diagnostics"])
        document=json.loads(path.with_name(path.name.replace(".MaterialMap.json",".json")).read_text(encoding="utf-8"))
        step=document["steps"][0]
        domains={d["id"]:profile_shape(_region_from_polygons(d["polygons"])) for d in step["domains"]}
        reports={l["name"]:l for l in meta["layers"] if l["status"]=="equivalent"}
        regions=meta["rectification"]["rectangles"]
        ids=meta["rectification"]["audit"]["domain_region_ids"]
        rect_ids={(r["x0_mm"],r["y0_mm"],r["x1_mm"],r["y1_mm"]):rid for rid,r in enumerate(regions)}
        xs,ys=meta["grid"]["x_lines_mm"],meta["grid"]["y_lines_mm"]
        xi,yi={v:i for i,v in enumerate(xs)},{v:j for j,v in enumerate(ys)}
        limits=meta["grid"]["size_constraints_mm"]
        bounds=meta["raster"]["board_bounds_mm"]
        px,py=meta["raster"]["px"],meta["raster"]["py"]
        ax=RasterAxis(bounds[0],(bounds[2]-bounds[0])/px,px)
        ay=RasterAxis(bounds[1],(bounds[3]-bounds[1])/py,py)
        required=meta["grid"]["mesh_configuration"]["minimum_samples_per_axis"]
        counts={}; minimum=10**9
        for region in materialmap.regions:
            name=region.layer.name; layer=reports[name]
            a,c=region.bounds_mm.x; b,d=region.bounds_mm.y
            rid=rect_ids[(a,b,c,d)]
            assert rid in ids[layer["domain_id"]]
            shape=box(a,b,c,d)
            assert shape.difference(domains[layer["domain_id"]]).area<1e-8
            assert shape.difference(domains[step["profile_domain_id"]]).area<1e-8
            expected_z=(layer["z_mm"]["start_mm"],layer["z_mm"]["end_mm"]) if layer["z_mm"]["thickness_known"] else (None,None)
            assert region.bounds_mm.z==expected_z
        for region,bounds5,values in iter_cells(materialmap):
            a,b,c,d=bounds5
            assert (a,b,c,d)==(xs[xi[a]],ys[yi[b]],xs[xi[a]+1],ys[yi[b]+1])
            for axis,length in enumerate((c-a,d-b)):
                assert limits["min"][axis]-1e-8<=length<=limits["max"][axis]+1e-8
            columns=ax.range_for(a,c);rows=ay.range_for(b,d)
            samples=min(columns[1]-columns[0],rows[1]-rows[0])
            assert samples>=required;minimum=min(minimum,samples)
            name=region.layer.name
            materials=meta["materials"]["by_layer"][name]
            phases=[materials["material_1_black"],materials["material_2_white"]]
            fields=("k_w_mk",)*3+("density_kg_m3","specific_heat_j_kgk")
            for value,field in zip(values,fields):
                lo,hi=sorted(p[field] for p in phases)
                assert math.isfinite(value) and value>0 and lo-1e-4<=value<=hi+1e-4
            counts[name]=counts.get(name,0)+1
        assert counts=={name:layer["cell_count"] for name,layer in reports.items()}
        records.append({"case":directory.relative_to(ROOT/"out").as_posix(),
            "region_count":len(regions),"physical_region_count":len(materialmap.regions),
            "nx":len(xs)-1,"ny":len(ys)-1,"grid_cells":meta["grid"]["cell_count"],
            "material_rows":sum(counts.values()),"raster":[px,py],
            "minimum_samples_per_axis":minimum,"area_loss_mm2":meta["rectification"]["lost_area_mm2"],
            "layer_count":len(reports),"drill_scope":meta["drill_scope"],
            "copper_drill_overlays_ignored":True,"all_checks_passed":True})
        print(records[-1],flush=True)
    assert records,"no MaterialMap JSON outputs to audit"
    return records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker",action="store_true")
    parser.add_argument("--baseline",action="store_true")
    parser.add_argument("--grid-level",choices=("coarse","standard","fine"))
    parser.add_argument("--auto-raster",action="store_true")
    parser.add_argument("--presets-only",action="store_true")
    parser.add_argument("--audit-out",action="store_true")
    parser.add_argument("--document",type=Path)
    parser.add_argument("--px",type=int)
    parser.add_argument("--py",type=int)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--baseline-ref",default="5aa06bf")
    args=parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.audit_out:
        output=args.output or ROOT/"docs"/"equivalence"/"output-validation.json"
        output.write_text(json.dumps({"schema":"odbpp/output-validation-5","cases":audit_outputs()},indent=2),encoding="utf-8")
        return
    import numpy as np
    if not args.presets_only: baseline_checkout(args.baseline_ref)
    scratch=ROOT/"build"/"architecture-validation"
    scratch.mkdir(exist_ok=True)
    destination=args.output or ROOT/"docs"/"equivalence"/"architecture-validation.json"
    results=json.loads(destination.read_text(encoding="utf-8"))["cases"] if args.presets_only else {}
    for case in ("designodb-rigidflex.tgz","kicad-kitchen-sink"):
        document=next(p for p in (ROOT/"out"/case).glob("*.json") if ".MaterialMap." not in p.name)
        records=[]
        values=[]
        configurations=[(400,400,True),(2400,1800,True),
                        (1200,900,False),(2400,1800,False),(4800,3600,False),(9600,7200,False)]
        if args.presets_only:
            records=[]
            for level in ("coarse","standard","fine"):
                output=scratch/f"{case}-{level}.json"
                subprocess.run([sys.executable,str(Path(__file__).resolve()),"--worker",
                                "--document",str(document),"--px","4096","--py","4096",
                                "--grid-level",level,"--auto-raster","--output",str(output)],cwd=ROOT,check=True)
                record=json.loads(output.read_text(encoding="utf-8"))
                assert not record["fatal"] and record["minimum_samples_per_axis"]>=8
                records.append(record);print(case,level,record,flush=True)
                output.unlink();output.with_suffix(".npy").unlink()
            results[case]["preset_runs"]=records
            continue
        for index,(px,py,old) in enumerate(configurations):
            output=scratch/f"{case}-{index}.json"
            command=[sys.executable,str(Path(__file__).resolve()),"--worker",
                     "--document",str(document),"--px",str(px),"--py",str(py),
                     "--output",str(output)]
            if old: command.append("--baseline")
            subprocess.run(command,cwd=ROOT,check=True)
            records.append(json.loads(output.read_text(encoding="utf-8")))
            values.append(np.load(output.with_suffix(".npy")))
            print(case,records[-1],flush=True)
            output.unlink()
            output.with_suffix(".npy").unlink()
        hashes={r["geometry_sha256"] for r in records[2:]}
        assert len(hashes)==1, "raster changed geometry"
        reference=values[-1]
        for record,value in zip(records[2:],values[2:]):
            if record["fatal"]:
                record["sampling_comparison"]="failed: raise resolution without altering geometry"
                continue
            relative=np.abs(value-reference)/reference
            record["relative_error_vs_9600x7200"]={
                name:{"median":float(np.median(relative[:,column])),
                      "p95":float(np.quantile(relative[:,column],.95)),
                      "max":float(relative[:,column].max())}
                for column,name in enumerate(("kx","ky","kz","density","specific_heat"))}
        results[case]={"geometry_invariant":True,"runs":records}
    output=args.output or ROOT/"docs"/"equivalence"/"architecture-validation.json"
    output.write_text(json.dumps({"schema":"odbpp/architecture-validation-5","baseline_ref":args.baseline_ref,"cases":results},indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
