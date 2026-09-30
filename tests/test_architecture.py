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


def test_local_boundary_is_not_extended_through_neighbor():
    rectangles=[__import__("odbpp.rectify",fromlist=["Rect"]).Rect(0,0,1,1),
                __import__("odbpp.rectify",fromlist=["Rect"]).Rect(1,0,21,10)]
    grid=build_grid(rectangles,GridOptions(1,1,max_cell_mm=5))
    body=[c for c in grid.cells if c.region_id==1]
    assert len(body)==8
    assert sorted({c.rect.y0 for c in body})==[0,5]


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
