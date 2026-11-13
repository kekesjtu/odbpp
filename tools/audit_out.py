"""Audit final MaterialMap JSON against source domains and run diagnostics.

Run: uv run --all-extras python tools/audit_out.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]



import math

class RasterAxis:
    def __init__(self,origin,step,count):
        self.origin,self.step,self.count=origin,step,count
    def range_for(self,low,high):
        return tuple(min(self.count,max(0,math.ceil((v-self.origin)/self.step-.5-1e-9))) for v in (low,high))

def physical_shape(polygons):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    islands=[Polygon(p['points']) for p in polygons if p['island']]
    holes={i:[] for i in range(len(islands))}
    for ring in (p for p in polygons if not p['island']):
        hole=Polygon(ring['points'])
        owners=[i for i,p in enumerate(islands) if p.covers(hole)]
        owner=min(owners,key=lambda i:islands[i].area)
        holes[owner].append(ring['points'])
    return unary_union([Polygon(p.exterior.coords,holes[i]) for i,p in enumerate(islands)])

def _components(shape):
    if shape.is_empty:return []
    return [shape] if shape.geom_type=='Polygon' else [p for p in shape.geoms if p.geom_type=='Polygon']

def _topology_source(original):
    from shapely.geometry import Polygon
    sources = _components(original)
    holes = [Polygon(h) for s in sources for h in s.interiors]
    cores = [(radius, _components(original.buffer(-radius)))
             for radius in (0.03125, 0.1, 0.25, 0.5, 1.0)]
    return sources, holes, cores


def _topology(original, model, source=None):
    from shapely.geometry import Polygon
    sources, holes, eroded = source if source is not None else _topology_source(original)
    targets = _components(model)
    matches = [sum(s.intersection(t).area > 1e-10 for t in targets) for s in sources]
    reverse_matches = [sum(s.intersection(t).area > 1e-10 for s in sources) for t in targets]
    model_holes = [Polygon(h) for t in targets for h in t.interiors]
    hole_matches = [sum(m.intersection(h).area > 1e-10 for m in model_holes) for h in holes]
    reverse_hole_matches = [sum(m.intersection(h).area > 1e-10 for h in holes) for m in model_holes]
    positive = all(len(_components(t.buffer(-1e-7))) == 1 for t in targets)
    # Erosion splits lobes separated by a narrow neck. Preserve every such
    # geometric core, including terminal branches within a single source island.
    # These scales are physical millimetres, entirely independent of sampling.
    core_checks = []
    for radius, cores in eroded:
        overlaps = [c.intersection(model) for c in cores]
        kept = [not overlap.is_empty and overlap.area > 1e-10 for overlap in overlaps]
        core_checks.append({"erosion_mm":radius,"cores":len(cores),"retained":sum(kept)})
    core_preserved = all(c["cores"] == c["retained"] for c in core_checks)
    return {"original_components": len(sources), "model_components": len(targets),
            "component_correspondence": matches,
            "original_holes": len(holes), "model_holes": len(model_holes),
            "hole_correspondence": hole_matches, "positive_width_connections": positive,
            "correspondence":"positive-area overlap",
            "protected_core_checks":core_checks,
            "preserved": all(n == 1 for n in matches + hole_matches + reverse_matches + reverse_hole_matches)
                         and len(targets) == len(sources) and len(model_holes) == len(holes)
                         and positive and core_preserved}


def audit_outputs(out_dir: Path = ROOT / "out"):
    """Reconstruct row-major JSON cells and independently audit vector/sampling invariants."""
    import math
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pcbeq_rust.materialmap import read_document, iter_cells
    records=[]
    for path in sorted(out_dir.rglob("materialmap.json")):
        directory=path.parent
        meta=json.loads((directory/"diagnostics/run.json").read_text(encoding="utf-8"))
        materialmap=read_document(path)
        document=json.loads((directory.parent / "pcbkit/intermediate.json").read_text(encoding="utf-8"))
        step=document["steps"][0]
        domains={d["id"]:physical_shape(d["polygons"]) for d in step["domains"]}
        source_layers={l["name"]:l for l in step["layers"]}
        reports={l["name"]:l for l in meta["layers"] if "domain_id" in l}
        geometry=meta["rectification"]
        profiles=geometry["profiles"]
        ratio=geometry["max_area_error_ratio"]
        def region_box(region):
            x0,x1=region.bounds_mm.x; y0,y1=region.bounds_mm.y
            return (x0,y0,x1,y1)
        for domain_id,canonical in geometry["domain_profiles"].items():
            source=domains[domain_id].intersection(domains[step["profile_domain_id"]])
            shapes=[box(*region_box(materialmap.regions[i])) for i in profiles[canonical]["regions"]]
            modeled=unary_union(shapes)
            assert source.symmetric_difference(modeled).area<=source.area*ratio+1e-8
            assert abs(sum(p.area for p in shapes)-modeled.area)<1e-7
            assert _topology(source,modeled)["preserved"]
        rect_ids={canonical:{region_box(materialmap.regions[i]):i for i in profile["regions"]}
                  for canonical,profile in profiles.items()}
        raster=meta["raster"]
        tiles={r["region"]:r for r in raster["regions"]}
        limits=meta["grid"]["max_cell_mm"]
        required=meta["grid"]["minimum_samples_per_axis"]
        for name,layer in reports.items():
            source=source_layers[name]
            if source["type"]=="DIELECTRIC":
                expected={d["name"] for d in source_layers.values()
                          if d["type"]=="DRILL" and d.get("context","BOARD")=="BOARD"
                          and source_layers[d["start_layer"]]["row"]<source["row"]<source_layers[d["end_layer"]]["row"]}
                assert set(layer["drill_overlays"])==expected
        counts={}; minimum=10**9
        for region in materialmap.regions:
            name=region.layer.name
            source=source_layers[name]
            z=source["z_mm"]
            expected_z=(z["start_mm"],z["end_mm"]) if z["thickness_known"] else (None,None)
            assert region.bounds_mm.z==expected_z
            if not z["thickness_known"]:
                estimate=reports[name]["estimated_z_mm"]
                assert (estimate["start_mm"],estimate["end_mm"])==(z["start_mm"],z["end_mm"])
        for region,bounds,values in iter_cells(materialmap):
            a,b,c,d=bounds
            for axis,length in enumerate((c-a,d-b)):
                assert 0<length<=limits[axis]+1e-8
            name=region.layer.name
            canonical=geometry["domain_profiles"][reports[name]["domain_id"]]
            tile=tiles[rect_ids[canonical][region_box(region)]]
            ax=RasterAxis(tile["origin_mm"][0],tile["pixel_step_mm"][0],tile["px"])
            ay=RasterAxis(tile["origin_mm"][1],tile["pixel_step_mm"][1],tile["py"])
            columns=ax.range_for(a,c);rows=ay.range_for(b,d)
            samples=min(columns[1]-columns[0],rows[1]-rows[0])
            assert samples>=required;minimum=min(minimum,samples)
            materials=meta["materials"][name]
            fields=("k_w_mk",)*3+("density_kg_m3","specific_heat_j_kgk")
            for value,field in zip(values,fields):
                lo,hi=sorted(materials[phase][field]["value"] for phase in ("copper","base"))
                assert math.isfinite(value) and value>0 and lo-1e-4<=value<=hi+1e-4
            counts[name]=counts.get(name,0)+1
        assert set(counts)==set(reports)
        records.append({"case":directory.relative_to(out_dir).as_posix(),
            "physical_regions":len(materialmap.regions),"material_rows":sum(counts.values()),
            "minimum_samples_per_axis":minimum,
            "max_profile_area_error_ratio":max((p["lost_area_mm2"]+p["gained_area_mm2"])/p["original_area_mm2"] for p in profiles.values())})
        print(records[-1],flush=True)
    assert records,"no MaterialMap JSON outputs to audit"
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "out",
                        help="case-output root to audit")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "tmp/validation/reports/output-validation.json")
    args = parser.parse_args()
    report = {"schema": "odbpp/output-validation-5", "cases": audit_outputs(args.out)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
