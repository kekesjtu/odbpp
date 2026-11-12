"""Differential and analytical acceptance of the compiled numerical boundary."""
import math

import pytest

pcbkit_rust = pytest.importorskip("pcbkit_rust")


def test_native_products_do_not_require_context_switches():
    assert pcbkit_rust._pcbkit_native is not None


@pytest.mark.parametrize("clockwise", [True,False])
@pytest.mark.parametrize("end", [(1.,0.),(0.,1.),(-1.,0.),(.000001,1.)])
def test_arc_endpoints_and_finite_coordinates(clockwise,end):
    result=pcbkit_rust._pcbkit_native.arc_points((1.,0.),end,(0.,0.),clockwise,.001,100000)
    assert result and result[-1]==end
    assert all(math.isfinite(v) for p in result for v in p)
