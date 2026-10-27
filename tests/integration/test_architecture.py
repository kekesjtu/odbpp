"""End-to-end geometric invariants independent of material sampling."""
import pytest
from shapely.geometry import box
from pcbeq.equiv import EquivalenceOptions, equivalence_from_document, plan_geometry
from pcbeq.rectify import profile_shape
from pcbeq.grid import GridOptions, build_grid
from pcbeq.diagnostics import Diagnostics
from test_equiv import build_document, rect, hole


def coordinates(result):
    return ([r.bounds for r in result.rectification.rectangles],
            [(c.region_id,c.bounds) for c in result.grid.cells])


def test_resolution_does_not_change_region_or_cell_coordinates():
    d=build_document(domains=[{"id":"step_profile","source":"step_profile","polygons":[
        {"island":True,"points":[[0,0],[30,0],[30,20],[10,20]]}]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(5,1,6,18)]}])
    results=[equivalence_from_document(d,EquivalenceOptions(GridOptions(p,p)))
             for p in (2,200,400)]
    assert coordinates(results[0]) == coordinates(results[1]) == coordinates(results[2])
    assert results[0].fatal and not results[1].fatal


def test_region_boundary_propagates_into_global_cartesian_axes():
    rectangles=[__import__("pcbeq.rectify",fromlist=["Rect"]).Rect(0,0,1,1),
                __import__("pcbeq.rectify",fromlist=["Rect"]).Rect(1,0,21,10)]
    grid=build_grid(rectangles,GridOptions(1,1,max_cell_mm=5))
    body=[c for c in grid.cells if c.region_id==1]
    assert len(body)==12
    assert sorted({c.rect.y0 for c in body})==[0,1,5.5]


def test_rigidflex_cells_are_complete_in_each_layer_and_share_region_ids():
    domains=[{"id":"step_profile","source":"step_profile","polygons":[rect(0,0,10,10)]},
             {"id":"left","source":"layer_profile","polygons":[rect(0,0,5,10)]}]
    d=build_document(domains=domains,layers=[
       {"name":"ALL","type":"SIGNAL","polygons":[]},
       {"name":"LEFT","type":"DIELECTRIC","domain_id":"left","polygons":[]}])
    r=equivalence_from_document(d,EquivalenceOptions(GridOptions(200,200)))
    assert not r.fatal
    for c in r.cells:
        assert c.domain_coverage==1 and c.domain_area_mm2==c.cell_area_mm2
        if c.layer_name=="LEFT": assert c.x1<=5
        owner=r.rectification.rectangles[c.region_id]
        assert box(*owner.bounds).covers(box(c.x0,c.y0,c.x1,c.y1))
        assert c.z_end_mm>c.z_start_mm
    assert set(c.region_id for c in r.cells if c.layer_name=="LEFT").issubset(
           set(c.region_id for c in r.cells if c.layer_name=="ALL"))


def test_material_branch_uses_no_complex_booleans(monkeypatch):
    import pcbeq.equiv as module
    def forbidden(*args): raise AssertionError("material boolean")
    for name in ("union","difference","intersection"):
        # Guard future module-level calls without requiring unused imports.
        monkeypatch.setattr(module,name,forbidden,raising=False)
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[
           rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]}])
    r=equivalence_from_document(d,EquivalenceOptions(GridOptions(200,200)))
    assert not r.fatal
    assert all(.1<=c.copper_fraction<=1 for c in r.cells)


@pytest.mark.parametrize("target", ["CU", "DIEL"])
def test_scanline_composition_matches_boolean_reference_with_drill_overlap(target):
    import numpy as np
    from pcbeq.equiv import _region_from_polygons, _Scanline
    from pcbeq.geometry import union, intersection
    from pcbeq.raster import RasterAxis, region_edges
    from test_equiv import drill_document
    d=drill_document(layers=[
        {"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]},
        {"name":"DIEL","type":"DIELECTRIC","polygons":[rect(0,0,10,10)]},
        {"name":"D2","type":"DRILL","polygons":[rect(5,3,8,5)]},
        {"name":"D","type":"DRILL","polygons":[rect(3,3,7,5)]},
    ])
    masks={}
    result=equivalence_from_document(d,EquivalenceOptions(GridOptions(100,100)),
                                    mask_sink=lambda name,mask,pitch:masks.update({name:mask}))
    assert not result.fatal
    layers=d["steps"][0]["layers"]
    ink = [l for l in layers if (l["name"] == "CU" if target == "CU" else l["type"] == "DRILL")]
    reference=intersection(union(*[_region_from_polygons(l["polygons"]) for l in ink]),
                           _region_from_polygons([rect(0,0,10,10)]))
    scan=_Scanline(region_edges(reference))
    axis=RasterAxis(0,.1,100)
    expected=np.full((100,100),255,dtype=np.uint8)
    for b in range(100):
        for left,right in scan.spans_at(axis.centre(b)):
            a0,a1=axis.range_for(left,right)
            expected[b,a0:a1]=0
    actual=np.frombuffer(masks[target].pixels,dtype=np.uint8).reshape(100,100)
    assert np.array_equal(actual,expected)


def test_layer_type_material_configuration_precedence():
    from pcbeq.materials import MaterialBook
    book=MaterialBook({
        "global":{"base":{"k_w_mk":.4}},
        "layer_types":{"dielectric":{"base":{"k_w_mk":.2}},
                       "copper":{"base":{"k_w_mk":.6}}},
        "layers":{"OVERRIDE":{"base":{"k_w_mk":.8}}},
    })
    assert book.resolve("CU","copper").k2 == .6
    assert book.resolve("FR","dielectric").k2 == .2
    assert book.resolve("OVERRIDE","dielectric").k2 == .8


def test_presets_are_geometry_adaptive_and_independent_of_sampling():
    from pcbeq.mesh import resolve_mesh
    from pcbeq.rectify import Rect
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(0,1,10,1.2)]}])
    step=d["steps"][0]; regions=[Rect(0,0,10,10)]
    configs=[resolve_mesh(step,regions,GridOptions(10,20,mesh_preset=level))[1]
             for level in ("coarse","standard","fine")]
    assert configs[0]["target_max_cell_mm"]>configs[1]["target_max_cell_mm"]>configs[2]["target_max_cell_mm"]
    assert configs[0]["feature_estimate_representative_mm"]>0
    assert resolve_mesh(step,regions,GridOptions(100,200,mesh_preset="standard"))[1]==configs[1]


def test_default_raster_is_dense_and_presets_reject_few_samples():
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10)]}])
    default=equivalence_from_document(d,EquivalenceOptions(GridOptions(1,1,mesh_preset="standard",auto_raster=True)))
    low=equivalence_from_document(d,EquivalenceOptions(GridOptions(100,100,mesh_preset="standard")))
    assert not default.fatal and low.fatal
    assert default.raster_px>=4096 and min(c.pixel_cols for c in default.cells)>=8
    assert coordinates(default)==coordinates(low)
    for c in default.grid.cells:
        assert c.rect.x0==default.grid.x_lines[c.i]
        assert c.rect.x1==default.grid.x_lines[c.i+1]
        assert c.rect.y0==default.grid.y_lines[c.j]
        assert c.rect.y1==default.grid.y_lines[c.j+1]


def test_expert_mode_retains_physical_limits_and_fails_zero_samples():
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10)]}])
    low=equivalence_from_document(d,EquivalenceOptions(GridOptions(1,1,1)))
    high=equivalence_from_document(d,EquivalenceOptions(GridOptions(200,200,1)))
    assert low.fatal and not high.fatal
    assert coordinates(low)==coordinates(high)
    assert high.grid.mesh_configuration["mode"]=="expert"
    assert high.grid.size_constraints_mm=={"max":[1,1]}


def test_cli_default_preset_and_invalid_combinations(tmp_path):
    from pcbeq.cli import main
    import json
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10)]}])
    source=tmp_path/"board.json";source.write_text(json.dumps(d),encoding="utf-8")
    output=tmp_path/"output"
    common=["run",str(source),"-o",str(output),"--no-raster-png","--no-heatmap","--no-gallery"]
    assert main(common+["--px","100"])==2
    assert main(common+["--px","0","--py","100"])==2
    assert main(common+["--grid-level","fine","--max-cell-mm","1"])==2
    assert main(common)==0
    meta=json.loads((output/"diagnostics/run.json").read_text(encoding="utf-8"))
    assert "schema" not in meta and "cells" not in meta["grid"]
    assert meta["grid"]["mesh_configuration"]["preset"]=="standard"
    assert meta["grid"]["layout"]=="global-cartesian"
    assert meta["raster"]["px"]>=4096
    assert meta["rectification"]["audit"]["boundary_retreat"]["max_allowed_mm"] == 3.0
    assert meta["rectification"]["audit"]["relative_area_budget"] == .05


def test_cli_accuracy_parameters_validate_and_reach_published_diagnostics(tmp_path):
    from pcbeq.cli import main
    import json
    document = build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    source = tmp_path/"board.json"
    source.write_text(json.dumps(document),encoding="utf-8")
    output = tmp_path/"result"
    common = ["run",str(source),"-o",str(output),"--no-raster-png","--no-heatmap",
              "--no-gallery","--no-regions","--no-grids"]
    for extra in (["--max-boundary-retreat-mm","0"], ["--max-boundary-retreat-mm","nan"],
                  ["--max-area-loss-ratio","1"], ["--max-area-loss-ratio","-0.1"]):
        assert main(common+extra) == 2
    assert main(common+["--max-boundary-retreat-mm","0.15","--max-area-loss-ratio","0.01"]) == 0
    report = json.loads((output/"diagnostics/run.json").read_text(encoding="utf-8"))
    audit = report["rectification"]["audit"]
    assert audit["boundary_retreat"]["max_allowed_mm"] == .15
    assert audit["relative_area_budget"] == .01
    assert all(d["shared_boundary_retreat"]["within_limit"] for d in audit["domains"].values())


def test_pipeline_forwards_both_region_accuracy_controls():
    from tools.run_pipeline import build_parser,forwarded
    args = build_parser().parse_args(["board.zip","-o","out/example",
        "--max-boundary-retreat-mm","0.15","--max-area-loss-ratio","0.01"])
    parser_args,equivalent_args = forwarded(args)
    assert not parser_args
    assert equivalent_args == ["--max-boundary-retreat-mm","0.15","--max-area-loss-ratio","0.01"]


def test_shared_domain_retry_reduces_coarsening_without_relaxing_user_budgets(monkeypatch):
    import pcbeq.equiv as module
    document = build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[]}])
    original = module._plan_geometry_once
    attempts = []
    def fail_first(step,options,diagnostics):
        attempts.append(options.rectify)
        geometry,membership = original(step,options,diagnostics)
        if len(attempts) == 1:
            geometry.ok = False
            diagnostics.error("equiv.domain_geometry","injected shared-domain conflict")
        return geometry,membership
    monkeypatch.setattr(module,"_plan_geometry_once",fail_first)
    diagnostics = Diagnostics()
    geometry,_ = plan_geometry(document["steps"][0],EquivalenceOptions(GridOptions(100,100)),diagnostics)
    assert geometry.ok and not diagnostics.errors
    assert [o.coarsening_retreat_mm for o in attempts] == [3.0,1.5]
    assert all(o.max_boundary_retreat_mm == 3.0 and o.max_relative_area_error == .05 for o in attempts)
    assert len(geometry.audit["shared_domain_replanning"]) == 2


def test_exact_shared_cleanup_preserves_every_physical_domain_union():
    from shapely.ops import unary_union
    from pcbeq.equiv import _merge_active_partition
    from pcbeq.rectify import Rect
    rows=[Rect(0,0,1,2),Rect(1,0,2,1),Rect(1,1,2,2),Rect(2,0,3,2),Rect(3,0,4,2)]
    members={"step":list(range(5)),"left":[1,2,3],"right":[4]}
    result,actual,audit=_merge_active_partition(rows,members,{"left","right"})
    assert audit=={"reference_only_pieces_removed":1,"exact_membership_merges":2}
    for key in ("left","right"):
        before=unary_union([box(*rows[i].bounds) for i in members[key]])
        after=unary_union([box(*result[i].bounds) for i in actual[key]])
        assert before.symmetric_difference(after).area==0
    assert [r.bounds for r in result]==[(1,0,3,2),(3,0,4,2)]


def test_shared_common_interval_merge_handles_stacked_layer_memberships():
    from shapely.ops import unary_union
    from pcbeq.equiv import _coarsen_shared_partition
    from pcbeq.rectify import Rect,RectifyOptions,boundary_retreat
    rows=[Rect(0,0,1,1.23456),Rect(1,0,2,1.23457),Rect(1,1.23457,2,1.23459)]
    members={"step":[0,1,2],"flex":[0,1],"rigid":[1,2]}
    originals={key:unary_union([box(*rows[i].bounds) for i in ids]) for key,ids in members.items()}
    result,actual,audit=_coarsen_shared_partition(rows,members,originals,RectifyOptions())
    assert audit["accepted_adjacent_common_intervals"]==1
    assert [r.bounds for r in result]==[(0,0,1,1.23456),(1,0,2,1.23456)]
    assert actual["flex"]==[0,1] and actual["rigid"]==[1]
    for key,source in originals.items():
        modeled=unary_union([box(*result[i].bounds) for i in actual[key]])
        assert modeled.difference(source).area==0
        assert source.difference(modeled).area<=source.area*.05
        assert boundary_retreat(source,modeled,3)["within_limit"]


def test_shared_common_interval_merge_cannot_disconnect_a_branch():
    from shapely.ops import unary_union
    from pcbeq.equiv import _coarsen_shared_partition
    from pcbeq.rectify import Rect,RectifyOptions
    rows=[Rect(0,0,1,1),Rect(1,0,2,.4),Rect(1,.6,2,1)]
    members={"board":[0,1,2]}
    source=unary_union([box(*r.bounds) for r in rows])
    result,_,audit=_coarsen_shared_partition(rows,members,{"board":source},
                                           RectifyOptions(max_relative_area_error=.9))
    assert [r.bounds for r in result]==[r.bounds for r in rows]
    assert audit["constraint_rejections"]>0


def test_sharing_one_true_limit_does_not_force_an_excessive_other_edge_retreat():
    from shapely.ops import unary_union
    from pcbeq.equiv import _coarsen_shared_partition
    from pcbeq.rectify import Rect,RectifyOptions
    rows=[Rect(0,0,1,10),Rect(1,.001,2,14)]
    source=unary_union([box(*r.bounds) for r in rows])
    result,_,audit=_coarsen_shared_partition(rows,{"board":[0,1]},
                                           {"board":source},RectifyOptions())
    assert [r.bounds for r in result]==[(0,.001,1,10),(1,.001,2,14)]
    assert audit["accepted_adjacent_common_intervals"]==1


def test_reference_only_step_pieces_do_not_create_material_or_resize_raster():
    domains=[{"id":"step_profile","source":"step_profile","polygons":[rect(0,0,10,10)]},
             {"id":"left","source":"layer_profile","polygons":[rect(0,0,5,10)]}]
    document=build_document(domains=domains,layers=[
        {"name":"LEFT","type":"SIGNAL","domain_id":"left","polygons":[]}])
    result=equivalence_from_document(document,EquivalenceOptions(GridOptions(200,200)))
    assert not result.fatal
    assert result.board_bounds_mm==(0,0,10,10)
    assert [r.bounds for r in result.rectification.rectangles]==[(0,0,5,10)]
    assert all(c.x1<=5 for c in result.cells)
    assert result.rectification.relative_error==0
