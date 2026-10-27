"""Validate the two region budgets and raster-independent planning on local real cases."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
CASES = ("kicad-kitchen-sink", "kicad-video-odb.tgz", "designodb-rigidflex")


def publication_metrics(name, coordinates, retreat, ratio):
    path = ROOT/"out"/name/"pcbeq/diagnostics/run.json"
    if not path.is_file():
        return None
    meta = json.loads(path.read_text(encoding="utf-8"))
    audit = meta["rectification"]["audit"]
    rectangles = [tuple(r[k] for k in ("x0_mm","y0_mm","x1_mm","y1_mm"))
                  for r in meta["rectification"]["rectangles"]]
    published = (rectangles,meta["grid"]["x_lines_mm"],meta["grid"]["y_lines_mm"])
    if (published != coordinates or audit.get("boundary_retreat",{}).get("max_allowed_mm") != retreat
            or audit.get("relative_area_budget") != ratio):
        return None
    return {**meta["runtime"],"measurement":"full pcbeq CLI, including imports, IO, images; native process peak",
            "raster":[meta["raster"]["px"],meta["raster"]["py"]],
            "material_cells":sum(l["cell_count"] for l in meta["layers"] if l["status"]=="equivalent")}


def sampling_convergence(name, retreat, ratio, initial_step=None):
    import numpy as np
    from pcbeq.equiv import EquivalenceOptions, equivalence_from_document
    from pcbeq.grid import GridOptions
    from pcbeq.rectify import RectifyOptions
    path = ROOT/"out"/name/"pcbkit/intermediate.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    options = EquivalenceOptions(GridOptions(4096,4096,mesh_preset="standard",auto_raster=True),
        rectify=RectifyOptions(max_boundary_retreat_mm=retreat,max_relative_area_error=ratio,
                               tolerance_mm=initial_step))
    arrays,runs,coordinates = [],[],[]
    for level in range(2):
        rows = []
        keys = []
        def sink(cell):
            rows.append([cell.kx,cell.ky,cell.kz,cell.density,cell.specific_heat])
            keys.append((cell.layer_name,cell.region_id,cell.x0,cell.y0,cell.x1,cell.y1))
        start = time.perf_counter()
        result = equivalence_from_document(document,options,cell_sink=sink)
        assert not result.fatal, [str(e) for e in result.diagnostics.errors]
        values = np.asarray(rows)
        assert np.isfinite(values).all() and (values>0).all()
        arrays.append(values)
        coordinates.append((keys,[r.bounds for r in result.rectification.rectangles],
                            result.grid.x_lines,result.grid.y_lines))
        runs.append({"raster":[result.raster_px,result.raster_py],
                     "seconds":time.perf_counter()-start,"material_cells":len(rows)})
        options = replace(options,grid=replace(options.grid,px=2*result.raster_px,
                                              py=2*result.raster_py,auto_raster=False))
    assert coordinates[0] == coordinates[1]
    differences = np.abs(arrays[1]-arrays[0])
    relative = differences/arrays[1]
    return {"runs":runs,"complete_cell_and_region_coordinates_identical":True,
        "comparison":"default vs doubled X/Y pixels; observed differences, not a universal convergence guarantee",
        "properties":{name:{"relative_p50":float(np.percentile(relative[:,i],50)),
            "relative_p95":float(np.percentile(relative[:,i],95)),
            "relative_max":float(relative[:,i].max()),"absolute_max":float(differences[:,i].max())}
            for i,name in enumerate(("kx","ky","kz","density","specific_heat"))}}


def validate_case(name, retreat, ratio, baseline=None, initial_step=None):
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pcbeq.diagnostics import Diagnostics
    from pcbeq.equiv import EquivalenceOptions, plan_geometry, _region_from_polygons
    from pcbeq.grid import GridOptions, build_grid
    from pcbeq.mesh import resolve_mesh
    from pcbeq.rectify import RectifyOptions, profile_shape, _boundary_pairs, boundary_retreat

    path = ROOT/"out"/name/"pcbkit/intermediate.json"
    step = json.loads(path.read_text(encoding="utf-8"))["steps"][0]
    options = EquivalenceOptions(GridOptions(4096,4096,mesh_preset="standard"),
        rectify=RectifyOptions(max_boundary_retreat_mm=retreat,max_relative_area_error=ratio,
                               tolerance_mm=initial_step))

    def plan(settings):
        diagnostics = Diagnostics()
        geometry,membership = plan_geometry(step,settings,diagnostics)
        assert geometry.ok, [str(e) for e in diagnostics.errors]
        mesh,config = resolve_mesh(step,geometry.rectangles,settings.grid)
        grid = build_grid(geometry.rectangles,mesh,diagnostics=diagnostics)
        assert not diagnostics.errors, [str(e) for e in diagnostics.errors]
        return geometry,membership,grid

    tracemalloc.start()
    start = time.perf_counter()
    geometry,membership,grid = plan(options)
    seconds = time.perf_counter()-start
    _,peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    coordinates = ([r.bounds for r in geometry.rectangles],grid.x_lines,grid.y_lines)
    changed,_,changed_grid = plan(replace(options,grid=replace(options.grid,px=8192,py=8192)))
    assert coordinates == ([r.bounds for r in changed.rectangles],changed_grid.x_lines,changed_grid.y_lines)

    domains = {d["id"]:profile_shape(_region_from_polygons(d["polygons"])) for d in step["domains"]}
    board = domains[step["profile_domain_id"]]
    domain_checks = []
    for domain_id,ids in membership.items():
        source = domains[domain_id].intersection(board)
        modeled = unary_union([box(*geometry.rectangles[i].bounds) for i in ids])
        assert modeled.difference(source).area <= 1e-8
        loss = source.area-modeled.area
        assert loss <= source.area*ratio+1e-8
        pairs = _boundary_pairs(source,modeled)
        assert pairs
        # An independently rebuilt, finer vector tube verifies continuous segment
        # coverage; no raster or sparse point sampling participates in this check.
        for a,b in pairs:
            assert a.buffer(retreat,quad_segs=128).covers(b)
            assert b.buffer(retreat,quad_segs=128).covers(a)
        audit = geometry.audit["domains"][domain_id]
        assert audit["shared_topology"]["preserved"]
        domain_checks.append({"domain":domain_id,"area_loss_ratio":loss/source.area,
            "retreat_upper_mm":audit["shared_boundary_retreat"]["max_distance_upper_mm"],
            "topology_preserved":True,"continuous_reference_check":True})

    before = None
    if baseline:
        old = baseline["geometry"]
        old_model = unary_union([box(r["x0_mm"],r["y0_mm"],r["x1_mm"],r["y1_mm"])
                                 for r in old["rectangles"]])
        before = {k:v for k,v in baseline.items() if k not in ("geometry","errors")}
        before["retreat_upper_mm"] = boundary_retreat(board,old_model,retreat)["max_distance_upper_mm"]
    row = {"case":name,"input_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        "max_boundary_retreat_mm":retreat,"max_area_loss_ratio":ratio,
        "internal_initial_step_override_mm":initial_step,
        "before":before,"after":{"regions":len(geometry.rectangles),"xy_cells":len(grid.cells),
        "seconds":seconds,"python_peak_mib":peak/1024**2,"area_loss_ratio":geometry.relative_error,
        "retreat_upper_mm":geometry.audit["boundary_retreat"]["max_distance_upper_mm"],
        "refinement_steps":geometry.audit["refinement_count"]},
        "refinement_trace_sample":geometry.audit["local_refinement"][:6]+geometry.audit["local_refinement"][-6:],
        "domains":domain_checks,"raster_dimensions_compared":[[4096,4096],[8192,8192]],
        "region_and_grid_coordinates_identical":True,
        "geometry_sha256":hashlib.sha256(json.dumps(coordinates).encode()).hexdigest(),
        "published_run":publication_metrics(name,coordinates,retreat,ratio),
        "all_checks_passed":True}
    print(json.dumps({"case":name,**row["after"]},ensure_ascii=False),flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case",action="append",choices=CASES)
    parser.add_argument("--max-boundary-retreat-mm",type=float,default=.5)
    parser.add_argument("--max-area-loss-ratio",type=float,default=.05)
    parser.add_argument("--diagnostic-geometry-step-mm",type=float,
                        help="internal test seed to exercise worst-first refinement; not a third accuracy control")
    parser.add_argument("--baseline",type=Path)
    parser.add_argument("--sampling-convergence",action="store_true",
                        help="also compare default material sampling to doubled X/Y pixels")
    parser.add_argument("--output",type=Path,default=ROOT/"docs/equivalence/region-accuracy-validation.json")
    args = parser.parse_args()
    baseline = {r["case"]:r for r in json.loads(args.baseline.read_text(encoding="utf-8"))} if args.baseline else {}
    report = {"date":datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "measurement":"geometry planning + mesh with tracemalloc; Python allocation peak excludes native GEOS/NumPy allocations",
        "resolution_independence_scope":"complete region and global-grid planning, without material sampling",
        "cases":[]}
    for case in args.case or CASES:
        report["cases"].append(validate_case(case,args.max_boundary_retreat_mm,
            args.max_area_loss_ratio,baseline.get(case),args.diagnostic_geometry_step_mm))
        if args.sampling_convergence:
            report["cases"][-1]["material_sampling"] = sampling_convergence(case,
                args.max_boundary_retreat_mm,args.max_area_loss_ratio,args.diagnostic_geometry_step_mm)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
