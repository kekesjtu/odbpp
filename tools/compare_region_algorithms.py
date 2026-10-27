"""Plot actual staircase/quadtree geometry on the same real PCB inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from html import escape
import hashlib
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]
CASES=("kicad-kitchen-sink","kicad-video-odb.tgz","designodb-rigidflex")


def compare(name,maximum,retreat,ratio,directory):
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pcbeq.diagnostics import Diagnostics
    from pcbeq.equiv import EquivalenceOptions,plan_geometry,_region_from_polygons
    from pcbeq.grid import GridOptions,build_grid
    from pcbeq.mesh import resolve_mesh,resolve_raster
    from pcbeq.rectify import RectifyOptions,profile_shape,_boundary_pairs
    path=ROOT/"out"/name/"pcbkit/intermediate.json"
    step=json.loads(path.read_text(encoding="utf-8"))["steps"][0]
    domains={d["id"]:profile_shape(_region_from_polygons(d["polygons"])) for d in step["domains"]}
    board=domains[step["profile_domain_id"]]
    rows=[]
    for algorithm in ("staircase","quadtree"):
        options=EquivalenceOptions(GridOptions(4096,4096,maximum,auto_raster=True),
            rectify=RectifyOptions(max_boundary_retreat_mm=retreat,max_relative_area_error=ratio),
            region_algorithm=algorithm)
        diagnostics=Diagnostics();start=time.perf_counter()
        geometry,membership=plan_geometry(step,options,diagnostics)
        assert geometry.ok,[str(e) for e in diagnostics.errors]
        checks=[]
        for key,ids in membership.items():
            source=domains[key].intersection(board)
            if geometry.audit["domains"][key].get("shared_membership_source")=="supported_physical_domain_union":
                source=unary_union([domains[k].intersection(board) for k in geometry.audit["active_physical_domain_ids"]])
            model=unary_union([box(*geometry.rectangles[i].bounds) for i in ids])
            assert model.difference(source).area<=1e-8
            assert source.difference(model).area<=source.area*ratio+1e-8
            pairs=_boundary_pairs(source,model)
            assert pairs
            for a,b in pairs:
                assert a.buffer(retreat,quad_segs=128).covers(b)
                assert b.buffer(retreat,quad_segs=128).covers(a)
            assert geometry.audit["domains"][key]["shared_topology"]["preserved"]
            checks.append({"domain":key,"complete_contour_check":True,"inscribed":True,"topology_preserved":True})
        mesh,config=resolve_mesh(step,geometry.rectangles,options.grid)
        grid=build_grid(geometry.rectangles,mesh,diagnostics=diagnostics)
        assert not diagnostics.errors,[str(e) for e in diagnostics.errors]
        for cell in grid.cells:
            assert 0<cell.rect.width<=maximum[0]+1e-8
            assert 0<cell.rect.height<=maximum[1]+1e-8
        try:
            sampling={"ok":True,"raster":list(resolve_raster(board.bounds,grid,mesh,config))}
        except ValueError as error:
            sampling={"ok":False,"error":str(error)}
        if algorithm=="quadtree":
            finest=geometry.audit["finest_square_side_mm"]
            assert all(abs(r.width-r.height)<1e-8 and r.width>=finest-1e-8 for r in geometry.rectangles)
        coords=([r.bounds for r in geometry.rectangles],grid.x_lines,grid.y_lines)
        row={"algorithm":algorithm,"max_cell_mm":list(maximum),"regions":len(geometry.rectangles),
             "xy_cells":len(grid.cells),"area_loss_ratio":geometry.relative_error,
             "retreat_upper_mm":geometry.audit["boundary_retreat"]["max_distance_upper_mm"],
             "seconds_geometry_mesh_and_independent_checks":time.perf_counter()-start,
             "geometry_sha256":hashlib.sha256(json.dumps(coords).encode()).hexdigest(),
             "sampling_capacity":sampling,"domains":checks,"all_geometry_checks_passed":True,
             "geometry":geometry.as_dict()}
        rows.append(row)
        destination=directory/name
        destination.mkdir(parents=True,exist_ok=True)
        (destination/f"{algorithm}.geometry.json").write_text(json.dumps(row,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(name,algorithm,row["regions"],row["xy_cells"],row["area_loss_ratio"],sampling,flush=True)
    return {"case":name,"input_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "board_polygons":next(d["polygons"] for d in step["domains"] if d["id"]==step["profile_domain_id"]),
            "bounds":list(board.bounds),"algorithms":rows}


def draw(panel,case,row,detail=False):
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Rectangle,PathPatch
    from matplotlib.path import Path as PlotPath
    from shapely.geometry.polygon import orient
    from pcbeq.equiv import _region_from_polygons
    from pcbeq.rectify import profile_shape,_components
    source=profile_shape(_region_from_polygons(case["board_polygons"]))
    panel.set_facecolor("#f4f6f9")
    for p in _components(source):
        p=orient(p,sign=1)
        vertices=[];codes=[]
        for ring in (p.exterior,*p.interiors):
            pts=list(ring.coords)
            vertices.extend(pts);codes.extend([PlotPath.MOVETO]+[PlotPath.LINETO]*(len(pts)-2)+[PlotPath.CLOSEPOLY])
        panel.add_patch(PathPatch(PlotPath(vertices,codes),facecolor="#f6c5bd",edgecolor="none"))
    patches=[Rectangle((r["x0_mm"],r["y0_mm"]),r["x1_mm"]-r["x0_mm"],r["y1_mm"]-r["y0_mm"])
             for r in row["geometry"]["rectangles"]]
    panel.add_collection(PatchCollection(patches,facecolor="#91c7e8",edgecolor="#193f5a",linewidth=.28))
    for polygon in case["board_polygons"]:
        pts=polygon["points"]+polygon["points"][:1]
        panel.plot([p[0] for p in pts],[p[1] for p in pts],color="#ca493b",linestyle="--",linewidth=.8)
    x0,y0,x1,y1=case["bounds"]
    if detail:
        x1=x0+(x1-x0)*.4
    pad=max(x1-x0,y1-y0)*.025
    panel.set_xlim(x0-pad,x1+pad);panel.set_ylim(y0-pad,y1+pad)
    panel.set_aspect("equal");panel.set_xlabel("X / mm");panel.set_ylabel("Y / mm")
    label="竖条阶梯" if row["algorithm"]=="staircase" else "正方形四叉树"
    panel.set_title(f"{label} | {row['regions']:,} regions | loss {row['area_loss_ratio']:.2%}\n"
                    f"完整轮廓距离上界 {row['retreat_upper_mm']:.3f} mm"+(" · 局部放大" if detail else ""),fontsize=11)


def render(cases,directory,maximum):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts=font_manager.get_font_names()
    plt.rcParams["font.sans-serif"]=[next((f for f in ("Microsoft YaHei","SimHei","Noto Sans CJK SC") if f in fonts),"DejaVu Sans")]
    plt.rcParams["axes.unicode_minus"]=False
    fig,axes=plt.subplots(len(cases),2,figsize=(15,4.5*len(cases)),squeeze=False,layout="constrained")
    for i,case in enumerate(cases):
        for j,row in enumerate(case["algorithms"]):
            draw(axes[i,j],case,row)
            axes[i,j].text(.02,.02,case["case"],transform=axes[i,j].transAxes,fontsize=10,
                           bbox={"facecolor":"white","alpha":.85,"edgecolor":"none"})
        detail,panels=plt.subplots(2,2,figsize=(15,9),layout="constrained")
        for j,row in enumerate(case["algorithms"]):
            draw(panels[0,j],case,row);draw(panels[1,j],case,row,True)
        detail.suptitle(case["case"]+" · 实际 region 几何对比（不含物性网格）",fontsize=15)
        detail.savefig(directory/case["case"]/"comparison.png",dpi=170)
        detail.savefig(directory/case["case"]/"comparison.svg")
        plt.close(detail)
    fig.suptitle(f"实际几何分区对比 | 相同 max grid = {maximum[0]:g} / {maximum[1]:g} mm\n"
                 "蓝色：内接模型；深色线：region 边界；红色虚线：原始轮廓；浅红：省略的原始面积",fontsize=14)
    fig.savefig(directory/"comparison.png",dpi=170)
    fig.savefig(directory/"comparison.svg")
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case",action="append",choices=CASES)
    parser.add_argument("--max-cell-mm",nargs="+",type=float,default=[.5])
    parser.add_argument("--max-boundary-retreat-mm",type=float,default=3)
    parser.add_argument("--max-area-loss-ratio",type=float,default=.05)
    parser.add_argument("--output",type=Path,default=ROOT/"out/region-comparison")
    args=parser.parse_args()
    from pcbeq.grid import GridOptions
    maximum=GridOptions(4096,4096,args.max_cell_mm).max_cell_mm
    args.output.mkdir(parents=True,exist_ok=True)
    cases=[compare(name,maximum,args.max_boundary_retreat_mm,args.max_area_loss_ratio,args.output)
           for name in args.case or CASES]
    render(cases,args.output,maximum)
    report={"date":datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
            "max_cell_mm":list(maximum),"max_boundary_retreat_mm":args.max_boundary_retreat_mm,
            "max_area_loss_ratio":args.max_area_loss_ratio,
            "cases":[{**case,"algorithms":[{k:v for k,v in row.items() if k!="geometry"} for row in case["algorithms"]]}
                     for case in cases]}
    (args.output/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    links="".join(f'<p><a href="{escape(c["case"])}/comparison.svg">{escape(c["case"])}：矢量图及局部放大</a></p>' for c in cases)
    page='<!doctype html><html lang="zh"><meta charset="utf-8"><title>Region 算法对比</title><style>body{font:16px system-ui;margin:24px;background:#f4f6f9}img{width:100%;background:white}p{line-height:1.7}</style><h1>Region 几何算法对比</h1><p>相同输入、max grid 与 3 mm / 5% 预算。图中只显示实际 region，物性单元随后由全局笛卡尔栅划分。四叉树的方块可以大于 max grid，最细方块边长才由它确定。</p>'+links+'<a href="comparison.svg"><img src="comparison.png"></a><p><a href="validation.json">逐层几何及网格核查数据</a></p></html>'
    (args.output/"index.html").write_text(page,encoding="utf-8")
    print(args.output/"comparison.png",flush=True)


if __name__=="__main__":
    main()
