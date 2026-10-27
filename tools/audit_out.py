"""Audit final MaterialMap JSON against source domains and run diagnostics.

Run: uv run --all-extras python tools/audit_out.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit_outputs(out_dir: Path = ROOT / "out"):
    """Reconstruct row-major JSON cells and independently audit vector/sampling invariants."""
    import math
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pcbeq.equiv import _region_from_polygons, pipeline_fingerprint
    from pcbeq.rectify import profile_shape, _boundary_pairs
    from pcbeq.materialmap import read_document, iter_cells
    from pcbeq.raster import RasterAxis
    records=[]
    for path in sorted(out_dir.rglob("materialmap.json")):
        directory=path.parent
        meta=json.loads((directory/"diagnostics/run.json").read_text(encoding="utf-8"))
        materialmap=read_document(path)
        assert meta["pipeline_fingerprint"]==pipeline_fingerprint()
        assert meta["drill_model"]=="solid_copper" and meta["drill_scope"]=="dielectric_only"
        assert all(not l["drill_overlays"] for l in meta["layers"] if l["type"]!="DIELECTRIC")
        assert not any(d["level"]=="error" for d in meta["diagnostics"])
        document=json.loads((directory.parent / "pcbkit/intermediate.json").read_text(encoding="utf-8"))
        step=document["steps"][0]
        domains={d["id"]:profile_shape(_region_from_polygons(d["polygons"])) for d in step["domains"]}
        reports={l["name"]:l for l in meta["layers"] if l["status"]=="equivalent"}
        regions=meta["rectification"]["rectangles"]
        ids=meta["rectification"]["audit"]["domain_region_ids"]
        geometry_audit=meta["rectification"]["audit"]
        retreat=geometry_audit["boundary_retreat"]["max_allowed_mm"]
        ratio=geometry_audit["relative_area_budget"]
        region_shapes=[box(r["x0_mm"],r["y0_mm"],r["x1_mm"],r["y1_mm"]) for r in regions]
        for domain_id,members in ids.items():
            source=domains[domain_id].intersection(domains[step["profile_domain_id"]])
            if geometry_audit["domains"][domain_id].get("shared_membership_source") == "supported_physical_domain_union":
                source=unary_union([domains[key].intersection(domains[step["profile_domain_id"]])
                                    for key in geometry_audit["active_physical_domain_ids"]])
            modeled=unary_union([region_shapes[rid] for rid in members])
            assert source.area-modeled.area<=source.area*ratio+1e-8
            pairs=_boundary_pairs(source,modeled)
            assert pairs
            for a,b in pairs:
                assert a.buffer(retreat,quad_segs=128).covers(b)
                assert b.buffer(retreat,quad_segs=128).covers(a)
            assert geometry_audit["domains"][domain_id]["shared_boundary_retreat"]["within_limit"]
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
                assert 0<length<=limits["max"][axis]+1e-8
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
        records.append({"case":directory.relative_to(out_dir).as_posix(),
            "region_count":len(regions),"physical_region_count":len(materialmap.regions),
            "nx":len(xs)-1,"ny":len(ys)-1,"grid_cells":meta["grid"]["cell_count"],
            "material_rows":sum(counts.values()),"raster":[px,py],
            "minimum_samples_per_axis":minimum,"area_loss_mm2":meta["rectification"]["lost_area_mm2"],
            "max_boundary_retreat_mm":retreat,
            "retreat_upper_mm":geometry_audit["boundary_retreat"]["max_distance_upper_mm"],
            "max_area_loss_ratio":ratio,"continuous_contour_checks_passed":True,
            "layer_count":len(reports),"drill_scope":meta["drill_scope"],
            "copper_drill_overlays_ignored":True,"all_checks_passed":True})
        print(records[-1],flush=True)
    assert records,"no MaterialMap JSON outputs to audit"
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "out",
                        help="case-output root to audit")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "docs/equivalence/output-validation.json")
    args = parser.parse_args()
    report = {"schema": "odbpp/output-validation-5", "cases": audit_outputs(args.out)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
