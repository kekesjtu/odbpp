"""Differential and analytical acceptance of the compiled numerical boundary."""
import math

import numpy as np
import pytest

pcbkit_rust = pytest.importorskip("pcbkit_rust")
pcbeq_rust = pytest.importorskip("pcbeq_rust")
from pcbkit import numerical as parser_backend
from pcbeq import numerical as equivalent_backend
from pcbeq_rust import _pcbeq_native as native


def test_context_restores_on_nested_exception():
    with parser_backend.use(None), equivalent_backend.use(None):
        with pytest.raises(RuntimeError):
            with pcbkit_rust.backend(), pcbeq_rust.backend():
                assert parser_backend.current() is pcbkit_rust._pcbkit_native
                assert equivalent_backend.current() is native
                with parser_backend.use(None):
                    assert parser_backend.current() is None
                assert parser_backend.current() is pcbkit_rust._pcbkit_native
                raise RuntimeError("test")
        assert parser_backend.current() is equivalent_backend.current() is None


@pytest.mark.parametrize("clockwise", [True, False])
@pytest.mark.parametrize("end", [(1.,0.), (0.,1.), (-1.,0.), (.000001,1.)])
def test_arc_endpoints_and_discretization_match_python(clockwise, end):
    from pcbkit.geometry import arc_points
    with parser_backend.use(None):
        reference = arc_points((1.,0.), end, (0.,0.), clockwise)
    with pcbkit_rust.backend():
        result = arc_points((1.,0.), end, (0.,0.), clockwise)
    np.testing.assert_allclose(result, reference, rtol=0., atol=2e-15)
    assert result[-1] == end


def test_series_parallel_anisotropy_and_mass_weighting():
    # Two horizontal materials, 50% each. kx=arithmetic, ky=harmonic.
    values = native.integrate(bytes([0]*8+[255]*8), 4, 4, [(0,4,0,4)],
                              [400.,1.,8000.,2000.,400.,1000.], 1)[0]
    assert values == pytest.approx([.5,200.5,2/(1/400+1),200.5,5000.,520.])
    columns = bytes([0,0,255,255]*4)
    rotated = native.integrate(columns, 4, 4, [(0,4,0,4)],
                               [400.,1.,8000.,2000.,400.,1000.], 1)[0]
    assert rotated[1:3] == pytest.approx(values[2:0:-1])


def test_random_blocks_against_independent_numpy_reference():
    rng = np.random.default_rng(20261029)
    mask = np.where(rng.random((23,31)) < .37, 0, 255).astype(np.uint8)
    windows = [(0,9,0,7), (9,31,0,7), (1,28,7,23)]
    results = native.integrate(mask.tobytes(),31,23,windows,[385.,.3,8960.,1850.,385.,1100.],1)
    for (a,b,c,d), (phi,kx,ky,kz,density,cp) in zip(windows,results):
        black = mask[c:d,a:b] == 0
        p = black.mean()
        assert phi == pytest.approx(p)
        assert kx == pytest.approx(np.mean(1/(black.mean(axis=1)/385+(1-black.mean(axis=1))/.3)))
        assert ky == pytest.approx(np.mean(1/(black.mean(axis=0)/385+(1-black.mean(axis=0))/.3)))
        assert kz == pytest.approx(p*385+(1-p)*.3)
        assert density == pytest.approx(p*8960+(1-p)*1850)
        assert cp*density == pytest.approx(p*8960*385+(1-p)*1850*1100)


@pytest.mark.parametrize("pixels,window,minimum", [
    ([0,255,160,255],(0,2,0,2),1), ([0]*4,(0,0,0,2),1), ([0]*4,(0,2,0,2),3),
])
def test_incomplete_or_unsampled_cells_are_rejected(pixels, window, minimum):
    assert native.integrate(bytes(pixels),2,2,[window],[1.]*6,minimum) == [None]


def test_native_boundaries_validate_resources_and_finite_inputs():
    with pytest.raises(ValueError):
        native.allocate(0.,10.,1e-20)
    with pytest.raises(ValueError):
        native.allocate(0.,10.,math.nan)
    with pytest.raises(ValueError):
        native.envelope([[(math.nan,0.),(1.,0.),(0.,1.)]],0.,1.)
    with pytest.raises(ValueError):
        native.integrate(b"",1,1,[],[1.]*6,1)
    with pytest.raises(ValueError):
        native.integrate(b"\0",1,1,[(0,2,0,1)],[1.]*6,1)
    with pytest.raises(ValueError):
        native.rasterize([],None,[],(0.,1.,120_000_001),(0.,1.,1))


def test_local_cartesian_grids_match_python_with_signed_zero():
    from pcbeq.grid import GridOptions, build_grid
    from pcbeq.rectify import Rect
    rectangles = [Rect(-2.,-1.,-0.,2.),Rect(0.,0.,3.1,1.)]
    with equivalent_backend.use(None):
        expected = build_grid(rectangles, GridOptions(50,50,(.7,.4)))
    with pcbeq_rust.backend():
        actual = build_grid(rectangles, GridOptions(50,50,(.7,.4)))
    assert [r.as_dict() for r in actual.regions] == [r.as_dict() for r in expected.regions]
    assert [(c.region_id,c.i,c.j,c.bounds) for c in actual.cells] == [
        (c.region_id,c.i,c.j,c.bounds) for c in expected.cells]


def test_equivalence_masks_geometry_and_values_match_reference():
    from pcbeq.equiv import EquivalenceOptions, equivalence_from_document
    from pcbeq.grid import GridOptions
    from test_equiv import drill_document, rect, hole
    document = drill_document(layers=[
        {"name":"CU","polygons":[rect(0,0,10,10),hole(2,2,8,8),rect(4,4,6,6)]},
        {"name":"FR","type":"DIELECTRIC","polygons":[]},
        {"name":"D1","type":"DRILL","polygons":[rect(2,2,6,4)]},
        {"name":"D2","type":"DRILL","polygons":[rect(4,3,7,5)]},
    ])
    results, masks = [], []
    for backend in (equivalent_backend.use(None),pcbeq_rust.backend()):
        layer_masks = {}
        with backend:
            results.append(equivalence_from_document(document,EquivalenceOptions(GridOptions(103,97,3)),
                mask_sink=lambda name,mask,pitch: layer_masks.update({name:bytes(mask.pixels)})))
        masks.append(layer_masks)
    assert masks[0] == masks[1]
    assert not any(r.fatal for r in results)
    assert [c.bounds for c in results[0].grid.cells] == [c.bounds for c in results[1].grid.cells]
    for a,b in zip(results[0].cells,results[1].cells):
        assert a.property_values() == pytest.approx(b.property_values(),rel=1e-13)
    assert results[1].metadata()["numerical_backend"] == "rust"
    assert results[1].metadata()["native_resource_limits"]["max_raster_pixels"] == 120_000_000
