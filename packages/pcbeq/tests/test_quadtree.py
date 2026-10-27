"""Square leaves, exact layer membership and a hard max-grid refinement scale."""
import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from pcbeq.diagnostics import Diagnostics
from pcbeq.equiv import EquivalenceOptions, plan_geometry, equivalence_from_document
from pcbeq.grid import GridOptions, build_grid
from test_equiv import build_document, rect, hole


def plan(document, size=.25, pixels=200):
    options=EquivalenceOptions(GridOptions(pixels,pixels,size),region_algorithm="quadtree")
    diagnostics=Diagnostics()
    geometry,membership=plan_geometry(document["steps"][0],options,diagnostics)
    return geometry,membership,diagnostics


def test_square_tree_keeps_large_interior_regions_and_only_grid_subdivides_them():
    document=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    geometry,_,diags=plan(document,2.5)
    assert geometry.ok and not diags.errors
    assert [r.bounds for r in geometry.rectangles]==[(0,0,10,10)]
    grid=build_grid(geometry.rectangles,GridOptions(200,200,2.5))
    assert len(grid.cells)==16
    assert geometry.as_dict()["strategy"]=="inscribed-square-quadtree"


@pytest.mark.parametrize("size", [.5,.25,.125])
def test_slanted_board_is_inscribed_with_only_dyadic_square_leaves(size):
    points=[[0,0],[16,0],[16,16]]
    document=build_document(domains=[{"id":"step_profile","source":"step_profile",
        "polygons":[{"island":True,"points":points}]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    geometry,membership,diags=plan(document,size)
    assert geometry.ok and not diags.errors
    source=__import__("shapely.geometry",fromlist=["Polygon"]).Polygon(points)
    modeled=unary_union([box(*r.bounds) for r in geometry.rectangles])
    assert modeled.difference(source).area==0
    assert sum(r.area for r in geometry.rectangles)==modeled.area
    assert source.difference(modeled).area/source.area<=.05
    for r in geometry.rectangles:
        assert r.width==pytest.approx(r.height)
        assert r.width>=size-1e-12
        assert (r.width/size).is_integer()
    assert len(membership["step_profile"])==len(geometry.rectangles)
    assert geometry.audit["boundary_retreat"]["within_limit"]


def test_profile_hole_is_preserved_and_does_not_become_copper():
    document=build_document(domains=[{"id":"step_profile","source":"step_profile",
        "polygons":[rect(0,0,16,16),hole(6,6,10,10)]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    geometry,_,diags=plan(document,.5)
    assert geometry.ok and not diags.errors
    assert geometry.audit["topology"]["original_holes"]==1
    assert all(box(*r.bounds).intersection(box(6,6,10,10)).area==0 for r in geometry.rectangles)


def test_thin_neck_reports_accuracy_failure_at_max_grid_instead_of_refining_below_it():
    points=[(0,0),(4,0),(4,1.875),(8,1.875),(8,0),(12,0),(12,4),
            (8,4),(8,2.125),(4,2.125),(4,4),(0,4)]
    document=build_document(domains=[{"id":"step_profile","source":"step_profile",
        "polygons":[{"island":True,"points":points}]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    coarse,_,diags=plan(document,1)
    assert not coarse.ok and "quadtree.accuracy_unattainable" in diags.codes()
    assert all(r.width>=1 for r in coarse.rectangles)
    assert "--max-cell-mm" in str(diags.errors[0])
    fine,_,diags=plan(document,.125)
    assert fine.ok and not diags.errors
    assert fine.audit["topology"]["preserved"]


def test_shared_tree_retains_square_leaves_and_complete_layer_membership():
    domains=[{"id":"step_profile","source":"step_profile","polygons":[rect(0,0,10,10)]},
             {"id":"left","source":"layer_profile","polygons":[rect(0,0,5,10)]}]
    document=build_document(domains=domains,layers=[
        {"name":"ALL","type":"SIGNAL","polygons":[]},
        {"name":"LEFT","type":"DIELECTRIC","domain_id":"left","polygons":[]}])
    geometry,members,diags=plan(document,1.25)
    assert geometry.ok and not diags.errors and len(geometry.rectangles)==4
    assert all(r.width==r.height for r in geometry.rectangles)
    assert all(geometry.rectangles[i].x1<=5 for i in members["left"])
    assert len(members["left"])==2
    result=equivalence_from_document(document,EquivalenceOptions(GridOptions(200,200,1.25),region_algorithm="quadtree"))
    assert not result.fatal
    assert all(c.domain_coverage==1 and c.cell_area_mm2==c.domain_area_mm2 for c in result.cells)


def test_anisotropic_max_grid_uses_smaller_axis_limit_and_ignores_pixels():
    document=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    a,_,_=plan(document,(2,1),pixels=2)
    b,_,_=plan(document,(2,1),pixels=4000)
    assert [r.bounds for r in a.rectangles]==[r.bounds for r in b.rectangles]
    assert a.audit["finest_square_side_mm"]==1
    assert a.audit["resolved_max_cell_mm"]==[2,1]


def test_algorithm_switch_is_forwarded_and_minimum_grid_option_is_removed():
    from pcbeq.cli import build_parser
    from tools.run_pipeline import build_parser as pipeline_parser,forwarded
    args=build_parser().parse_args(["run","board.json","--region-algorithm","quadtree"])
    assert args.region_algorithm=="quadtree" and not hasattr(args,"min_cell_mm")
    assert "--min-cell-mm" not in build_parser().format_help()
    args=pipeline_parser().parse_args(["board.zip","-o","out/test","--region-algorithm","quadtree"])
    assert forwarded(args)[1]==["--region-algorithm","quadtree"]
    with pytest.raises(ValueError):
        EquivalenceOptions(GridOptions(10,10),region_algorithm="unknown")


def test_contour_and_area_budgets_are_not_relaxed_at_the_finest_level():
    from pcbeq.rectify import RectifyOptions
    document=build_document(domains=[{"id":"step_profile","source":"step_profile",
        "polygons":[{"island":True,"points":[[0,0],[16,0],[16,16]]}]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    for settings in (RectifyOptions(max_relative_area_error=.001),
                     RectifyOptions(max_boundary_retreat_mm=.05)):
        diagnostics=Diagnostics()
        geometry,_=plan_geometry(document["steps"][0],EquivalenceOptions(
            GridOptions(100,100,1),rectify=settings,region_algorithm="quadtree"),diagnostics)
        assert not geometry.ok and "quadtree.accuracy_unattainable" in diagnostics.codes()
        assert all(r.width>=1 for r in geometry.rectangles)


def test_quadtree_depth_limit_reports_a_resource_error():
    document=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    geometry,_,diags=plan(document,1e-20)
    assert not geometry.ok and not geometry.rectangles
    assert "quadtree.resource_limit" in diags.codes()


def test_cli_publishes_quadtree_regions_and_max_only_grid_metadata(tmp_path):
    import json
    from pcbeq.cli import main
    source=tmp_path/"board.json"
    source.write_text(json.dumps(build_document(layers=[
        {"name":"CU","type":"SIGNAL","polygons":[]}])) ,encoding="utf-8")
    destination=tmp_path/"quad"
    assert main(["run",str(source),"-o",str(destination),"--region-algorithm","quadtree",
                 "--max-cell-mm","1.25","--no-raster-png","--no-heatmap","--no-gallery"])==0
    report=json.loads((destination/"diagnostics/run.json").read_text(encoding="utf-8"))
    assert report["rectification"]["strategy"]=="inscribed-square-quadtree"
    assert report["grid"]["size_constraints_mm"]=={"max":[1.25,1.25]}
    assert "min_cell_mm" not in json.dumps(report)
    assert (destination/"regions/regions.png").is_file()
