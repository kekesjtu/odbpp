"""Differential and analytical acceptance of the compiled numerical boundary."""
import math

import numpy as np
import pytest

pcbkit_rust = pytest.importorskip("pcbkit_rust")
pcbeq_rust = pytest.importorskip("pcbeq_rust")
from pcbeq_rust import _pcbeq_native as native


def test_native_products_do_not_require_context_switches():
    assert pcbkit_rust._pcbkit_native is not None
    assert native.MAX_RASTER_PIXELS==120_000_000


@pytest.mark.parametrize("clockwise", [True,False])
@pytest.mark.parametrize("end", [(1.,0.),(0.,1.),(-1.,0.),(.000001,1.)])
def test_arc_endpoints_and_finite_coordinates(clockwise,end):
    result=pcbkit_rust._pcbkit_native.arc_points((1.,0.),end,(0.,0.),clockwise,.001,100000)
    assert result and result[-1]==end
    assert all(math.isfinite(v) for p in result for v in p)


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
        native.rasterize([(math.nan,0.,1.,1.)],None,[],(0.,1.,1),(0.,1.,1))
    with pytest.raises(ValueError):
        native.integrate(b"",1,1,[],[1.]*6,1)
    with pytest.raises(ValueError):
        native.integrate(b"\0",1,1,[(0,2,0,1)],[1.]*6,1)
    with pytest.raises(ValueError):
        native.rasterize([],None,[],(0.,1.,120_000_001),(0.,1.,1))


def test_local_cartesian_grids_keep_independent_uniform_axes():
    axes,cells=native.build_grid([(-2.,-1.,-0.,2.),(0.,0.,3.1,1.)],(.7,.4))
    assert [(len(x)-1,len(y)-1) for x,y in axes]==[(3,8),(5,3)]
    assert len(cells)==39
    assert axes[0][0][0]==-2 and axes[0][0][-1]==0
    assert axes[1][0][0]==0 and axes[1][0][-1]==3.1
