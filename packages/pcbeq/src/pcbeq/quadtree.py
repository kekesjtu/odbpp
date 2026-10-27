"""Shared square quadtree geometry, independent of material raster sampling."""
from __future__ import annotations

import heapq
import math

from shapely.geometry import box
from shapely.ops import unary_union
from shapely.prepared import prep

from .mesh import resolve_mesh
from .rectify import Rect, RectifyResult, profile_shape, profile_region, _topology, boundary_retreat

MAX_DEPTH = 20
MAX_NODES = 500_000


def plan_quadtree(step, options, diagnostics):
    from .equiv import COPPER_TYPES, DIELECTRIC_TYPE, _region_from_polygons

    domains={d["id"]:d for d in step["domains"]}
    profile_id=step["profile_domain_id"]
    board=profile_shape(_region_from_polygons(domains[profile_id]["polygons"]))
    physical={l.get("domain_id") for l in step["layers"]
              if l.get("context","BOARD")=="BOARD" and
              l.get("type") in COPPER_TYPES|{DIELECTRIC_TYPE}}
    physical.intersection_update(domains)
    originals={key:profile_shape(_region_from_polygons(domains[key]["polygons"])).intersection(board)
               for key in sorted(physical|{profile_id})}
    mesh,mesh_config=resolve_mesh(step,[],options.grid)
    finest=min(mesh.max_cell_mm)
    x0,y0,x1,y1=board.bounds
    ratio=max(x1-x0,y1-y0)/finest
    depth=max(0,math.ceil(math.log2(ratio))) if math.isfinite(ratio) and ratio>0 else MAX_DEPTH+1
    settings=options.rectify
    audit={"strategy":"inscribed-square-quadtree","y_quantization":False,
           "finest_square_side_mm":finest,"resolved_max_cell_mm":list(mesh.max_cell_mm),
           "square_scale_rule":"min(max_cell_x, max_cell_y)",
           "origin_mm":[x0,y0],"depth":depth,"max_depth":MAX_DEPTH,"max_nodes":MAX_NODES,
           "refinement_priority":"largest unresolved square first",
           "relative_area_budget":settings.max_relative_area_error,
           "area_budget_mm2":board.area*settings.max_relative_area_error,
           "mesh_basis":mesh_config,"local_refinement":[],"refinement_count":0}
    if depth>MAX_DEPTH:
        diagnostics.error("quadtree.resource_limit",
            f"quadtree depth {depth} exceeds {MAX_DEPTH}; resolved max grid={mesh.max_cell_mm} mm")
        return RectifyResult([],finest,board.area,0,board.area,0,1,False,audit=audit),{}

    count=2**depth
    # One tree for all layer domains: every accepted leaf has explicit, complete
    # domain membership. Coincident domains share classification work.
    shapes=[];shape_ids={};domain_shapes={}
    for key,shape in originals.items():
        if shape.wkb not in shape_ids:
            shape_ids[shape.wkb]=len(shapes);shapes.append(shape)
        domain_shapes[key]=shape_ids[shape.wkb]
    prepared=[prep(s) for s in shapes]
    def rectangle(x,y,n):
        return Rect(x0+x*finest,y0+y*finest,x0+(x+n)*finest,y0+(y+n)*finest)

    queue=[(-count*count,0,0,count)]
    leaves={};visited=split=boundary_leaves=0
    resource_ok=True
    while queue:
        if visited>=MAX_NODES:
            resource_ok=False
            diagnostics.error("quadtree.resource_limit",f"quadtree visited {visited} squares; limit={MAX_NODES}")
            break
        _,x,y,n=heapq.heappop(queue)
        visited+=1
        square=box(*rectangle(x,y,n).bounds)
        inside=set();partial=False
        for i,geometry in enumerate(shapes):
            if prepared[i].covers(square):
                inside.add(i)
            elif prepared[i].intersects(square) and geometry.intersection(square).area>0:
                partial=True
        if partial and n>1:
            half=n//2;split+=1
            for dx,dy in ((0,0),(half,0),(0,half),(half,half)):
                heapq.heappush(queue,(-half*half,x+dx,y+dy,half))
        else:
            if partial:
                boundary_leaves+=1
            leaves[x,y,n]=frozenset(inside)

    # Collapse only four sibling squares with exactly equal memberships. This
    # changes neither a domain's union nor its error; rectangles stay square.
    collapsed=0
    n=1
    while n<count:
        parents={(x//(2*n)*2*n,y//(2*n)*2*n) for x,y,k in leaves if k==n}
        for x,y in sorted(parents):
            children=[(x+dx,y+dy,n) for dx,dy in ((0,0),(n,0),(0,n),(n,n))]
            if all(c in leaves for c in children) and len({leaves[c] for c in children})==1:
                owners=leaves[children[0]]
                for child in children:
                    del leaves[child]
                leaves[x,y,2*n]=owners;collapsed+=1
        n*=2
    reference_rows=[rectangle(x,y,n) for (x,y,n),owners in leaves.items()
                    if domain_shapes[profile_id] in owners]
    tagged=[]
    for (x,y,n),owners in leaves.items():
        keys=frozenset(key for key,i in domain_shapes.items() if i in owners)
        if keys&physical:
            tagged.append((rectangle(x,y,n),keys))
    tagged.sort(key=lambda item:item[0].bounds)
    rectangles=[r for r,_ in tagged]
    membership={key:[i for i,(_,keys) in enumerate(tagged) if key in keys] for key in originals}
    supported=unary_union([originals[key] for key in physical]) if physical else board
    model=unary_union([box(*r.bounds) for r in rectangles])

    def check(source,modeled):
        lost=source.difference(modeled).area
        gained=modeled.difference(source).area
        topology=_topology(source,modeled)
        contour=boundary_retreat(source,modeled,settings.max_boundary_retreat_mm)
        good=(lost<=source.area*settings.max_relative_area_error+1e-8 and gained<=1e-8
              and topology["preserved"] and contour["within_limit"])
        return {"shared_original_area_mm2":source.area,"shared_model_area_mm2":modeled.area,
                "shared_loss_area_mm2":lost,"shared_gained_area_mm2":gained,
                "shared_topology":topology,"shared_boundary_retreat":contour,"ok":good}

    checks={}
    for key,ids in membership.items():
        source=supported if key==profile_id else originals[key]
        modeled=unary_union([box(*rectangles[i].bounds) for i in ids])
        checks[key]=check(source,modeled)
        if key==profile_id:
            checks[key]["shared_membership_source"]="supported_physical_domain_union"
    reference=check(board,unary_union([box(*r.bounds) for r in reference_rows]))
    root=check(supported,model)
    good=resource_ok and reference["ok"] and root["ok"] and all(d["ok"] for d in checks.values())
    audit.update({"visited_nodes":visited,"refinement_count":split,
        "boundary_leaves_at_finest_scale":boundary_leaves,"collapsed_sibling_groups":collapsed,
        "root_square_side_mm":count*finest,"domains":checks,"domain_region_ids":membership,
        "modeling_source":"supported_physical_domain_union","active_physical_domain_ids":sorted(physical),
        "reference_step_geometry":reference,"boundary_retreat":root["shared_boundary_retreat"],
        "topology":root["shared_topology"],"area_budget_mm2":supported.area*settings.max_relative_area_error,
        "inscribed":root["shared_gained_area_mm2"]<=1e-8,
        "non_overlapping":True,"partition_non_overlapping":True})
    if not good and resource_ok:
        failures=[key for key,report in checks.items() if not report["ok"]]
        if not reference["ok"]:
            failures.append("original step reference")
        diagnostics.error("quadtree.accuracy_unattainable",
            f"quadtree at resolved max grid={mesh.max_cell_mm} mm (finest square side {finest:g} mm) "
            f"cannot satisfy contour <= {settings.max_boundary_retreat_mm:g} mm, "
            f"loss <= {settings.max_relative_area_error:g}, inscription and topology; domains={failures}. "
            "Use a smaller --max-cell-mm or a finer --grid-level; geometry was not refined below this scale.")
    lost=root["shared_loss_area_mm2"];gained=root["shared_gained_area_mm2"]
    result=RectifyResult(rectangles,finest,supported.area,model.area,lost,gained,
        lost/supported.area if supported.area else 0,good,net_area_error_mm2=model.area-supported.area,
        shape_deviation_mm2=lost+gained,region=profile_region(model),audit=audit)
    return result,membership
