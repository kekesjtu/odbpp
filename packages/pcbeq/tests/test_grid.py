"""Board grid allocation and outline rectangularisation.

These are data-flow step 4's geometry preconditions, tested without the
equivalence itself: they only need :mod:`pcbeq.rectify` and :mod:`pcbeq.grid`.
"""

from __future__ import annotations

import pytest

from pcbeq.diagnostics import Diagnostics
from pcbeq.grid import GridOptions, build_grid
from pcbeq.rectify import Rect, RectifyOptions, rectify_region
from pcbeq.geometry import region_from_rings


class TestGrid:
    def test_limits_accept_shared_or_axis_values(self):
        assert GridOptions(100, 100).min_cell_mm == (0.1, 0.1)
        assert GridOptions(100, 100, max_cell_mm=[2, 3]).max_cell_mm == (2, 3)
        for low, high in [(0, 1), (2, 1), (float("nan"), 1), (1, float("inf"))]:
            with pytest.raises(ValueError):
                GridOptions(100, 100, low, high)
        with pytest.raises(ValueError):
            GridOptions(100, 100, max_cell_mm=[1, 2, 3])

    def test_wide_intervals_are_split_despite_dense_edges(self):
        rectangles = [Rect(x, 0, x+1, 10) for x in range(5)] + [Rect(5, 0, 70, 10)]
        grid = build_grid(rectangles, GridOptions(400, 400, 0.5, 5))
        assert grid.cells
        assert set(range(6)).issubset(grid.x_lines)
        assert len([c for c in grid.cells if c.rect.x0 >= 5]) == 13*2
        for cell in grid.cells:
            assert 0.5 <= cell.rect.width <= 5
            assert 0.5 <= cell.rect.height <= 5
            owner = [r for r in rectangles
                     if r.x0 <= cell.rect.x0 and cell.rect.x1 <= r.x1
                     and r.y0 <= cell.rect.y0 and cell.rect.y1 <= r.y1]
            assert len(owner) == 1
            assert owner[0] is rectangles[cell.region_id]

    @pytest.mark.parametrize("width,low,high", [(0.2,0.5,5), (5,3,4)])
    def test_incompatible_forced_interval_errors_with_coordinates(self, width, low, high):
        diagnostics = Diagnostics()
        grid = build_grid([Rect(0,0,width,5)], GridOptions(100,100,low,high),
                          diagnostics=diagnostics)
        assert not grid.cells
        assert "grid.size_incompatible" in diagnostics.codes()
        assert "X interval [0," in diagnostics.errors[0].message

    def test_bounds_rounding_and_per_axis_constraints(self):
        grid = build_grid([Rect(0,0,10,6)], GridOptions(100,100,(1,1),(3,2)))
        assert grid.nx == 4 and grid.ny == 3
        assert grid.x_lines == [0,2.5,5,7.5,10]
        assert grid.as_dict()["size_constraints_mm"] == {"min":[1,1],"max":[3,2]}

    @pytest.mark.parametrize("maximum", [2, 1e-300])
    def test_empty_sample_subcell_is_fatal(self, maximum):
        from pcbeq.raster import RasterAxis
        axis = RasterAxis(0, 5, 2)
        diagnostics = Diagnostics()
        grid = build_grid([Rect(0,0,10,10)], GridOptions(2,2,min(0.1,maximum),maximum),
                          diagnostics=diagnostics,
                          capacity_x=lambda a,b: axis.pixel_index(b)-axis.pixel_index(a))
        if maximum == 2:
            assert grid.cells  # geometry survives sampling failure unchanged
            assert "grid.cell_without_pixel" in diagnostics.codes()
        else:
            assert "grid.size_incompatible" in diagnostics.codes()
