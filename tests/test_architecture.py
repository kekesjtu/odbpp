"""End-to-end geometric invariants independent of material sampling."""
import pytest
from shapely.geometry import box
from odbpp.equiv import EquivalenceOptions, equivalence_from_document, plan_geometry
from odbpp.rectify import profile_shape
from odbpp.grid import GridOptions, build_grid
from odbpp.diagnostics import Diagnostics
from test_equiv import build_document, rect, hole


def coordinates(result):
    return ([r.bounds for r in result.rectification.rectangles],
            [(c.region_id,c.bounds) for c in result.grid.cells])


def test_resolution_does_not_change_region_or_cell_coordinates():
    d=build_document(domains=[{"id":"step_profile","polygons":[
        {"island":True,"points":[(0,0),(30,0),(30,20),(10,20)]}]}],
        layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(5,1,6,18)]}])
    results=[equivalence_from_document(d,EquivalenceOptions(GridOptions(p,p)))
             for p in (2,200,400)]
    assert coordinates(results[0]) == coordinates(results[1]) == coordinates(results[2])
    assert results[0].fatal and not results[1].fatal


def test_region_boundary_propagates_into_global_cartesian_axes():
    rectangles=[__import__("odbpp.rectify",fromlist=["Rect"]).Rect(0,0,1,1),
                __import__("odbpp.rectify",fromlist=["Rect"]).Rect(1,0,21,10)]
    grid=build_grid(rectangles,GridOptions(1,1,max_cell_mm=5))
    body=[c for c in grid.cells if c.region_id==1]
    assert len(body)==12
    assert sorted({c.rect.y0 for c in body})==[0,1,5.5]


def test_rigidflex_cells_are_complete_in_each_layer_and_share_region_ids():
    domains=[{"id":"step_profile","polygons":[rect(0,0,10,10)]},
             {"id":"left","polygons":[rect(0,0,5,10)]}]
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
    import odbpp.equiv as module
    def forbidden(*args): raise AssertionError("material boolean")
    for name in ("union","difference","intersection"):
        monkeypatch.setattr(module,name,forbidden)
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[
           rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]}])
    r=equivalence_from_document(d,EquivalenceOptions(GridOptions(200,200)))
    assert not r.fatal
    assert all(.1<=c.copper_fraction<=1 for c in r.cells)


def test_scanline_composition_matches_boolean_reference_with_drill_overlap():
    import numpy as np
    from odbpp.equiv import _region_from_polygons, _Scanline
    from odbpp.clipper import union, intersection
    from odbpp.raster import RasterAxis, region_edges
    d=build_document(layers=[
        {"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]},
        {"name":"D","type":"DRILL","polygons":[rect(3,3,7,5)],
         "z_mm":{"start_mm":0,"end_mm":.035,"thickness_known":True}},
    ])
    masks={}
    result=equivalence_from_document(d,EquivalenceOptions(GridOptions(100,100)),
                                    mask_sink=lambda name,mask,pitch:masks.update({name:mask}))
    assert not result.fatal
    layers=d["steps"][0]["layers"]
    reference=intersection(union(*[_region_from_polygons(l["polygons"]) for l in layers]),
                           _region_from_polygons([rect(0,0,10,10)]))
    scan=_Scanline(region_edges(reference))
    axis=RasterAxis(0,.1,100)
    expected=np.full((100,100),255,dtype=np.uint8)
    for b in range(100):
        for left,right in scan.spans_at(axis.centre(b)):
            a0,a1=axis.range_for(left,right)
            expected[b,a0:a1]=0
    actual=np.frombuffer(masks["CU"].pixels,dtype=np.uint8).reshape(100,100)
    assert np.array_equal(actual,expected)


def test_layer_type_material_configuration_precedence():
    from odbpp.materials import MaterialBook
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
    from odbpp.mesh import resolve_mesh
    from odbpp.rectify import Rect
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
    low=equivalence_from_document(d,EquivalenceOptions(GridOptions(1,1,.1,1)))
    high=equivalence_from_document(d,EquivalenceOptions(GridOptions(200,200,.1,1)))
    assert low.fatal and not high.fatal
    assert coordinates(low)==coordinates(high)
    assert high.grid.mesh_configuration["mode"]=="expert"
    assert high.grid.size_constraints_mm=={"min":[.1,.1],"max":[1,1]}


def test_cli_default_preset_and_invalid_combinations(tmp_path):
    from odbpp.cli import main
    import json
    d=build_document(layers=[{"name":"CU","type":"SIGNAL","polygons":[rect(0,0,10,10)]}])
    source=tmp_path/"board.json";source.write_text(json.dumps(d),encoding="utf-8")
    output=tmp_path/"output"
    common=["equivalent","--from-json",str(source),"-o",str(output),"--no-raster-png","--no-heatmap","--no-gallery"]
    assert main(common+["--px","100"])==2
    assert main(common+["--px","0","--py","100"])==2
    assert main(common+["--grid-level","fine","--min-cell-mm",".1"])==2
    assert main(common)==0
    meta=json.loads(next(output.glob("*.meta.json")).read_text(encoding="utf-8"))
    assert meta["schema"]=="odbpp/materialmap-5"
    assert meta["grid"]["mesh_configuration"]["preset"]=="standard"
    assert meta["grid"]["layout"]=="global-cartesian"
    assert meta["raster"]["px"]>=4096
