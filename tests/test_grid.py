"""Board grid allocation and outline rectangularisation.

These are data-flow step 4's geometry preconditions, tested without the
equivalence itself: they only need :mod:`odbpp.rectify` and :mod:`odbpp.grid`.
"""

from __future__ import annotations

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.grid import GridOptions, build_grid
from odbpp.rectify import Rect, RectifyOptions, rectify_region
from odbpp.clipper import region_from_rings


class TestGrid:
    def test_nx_ny_default_to_the_pixel_counts(self):
        assert GridOptions(px=37, py=19).cells_x == 37
        assert GridOptions(px=37, py=19).cells_y == 19

    def test_explicit_counts_outside_the_pixel_range_are_rejected(self):
        with pytest.raises(ValueError):
            GridOptions(px=10, py=10, nx=11)
        with pytest.raises(ValueError):
            GridOptions(px=10, py=10, ny=0)

    def test_every_cell_lies_inside_exactly_one_region(self):
        rectangles = [Rect(0, 0, 4, 4), Rect(4, 0, 10, 6)]
        grid = build_grid(rectangles, GridOptions(px=20, py=20, nx=20, ny=20))
        assert grid.cells
        for cell in grid.cells:
            owner = [r for r in rectangles
                     if r.x0 <= cell.rect.x0 and cell.rect.x1 <= r.x1
                     and r.y0 <= cell.rect.y0 and cell.rect.y1 <= r.y1]
            assert len(owner) == 1, cell
            assert owner[0] is rectangles[cell.region_id]

    def test_forced_boundaries_that_do_not_fit_are_an_error_not_extra_cells(self):
        # 6 forced x boundaries need 5 cells; asking for 3 must fail rather than
        # silently change nx.
        rectangles = [Rect(x, 0, x + 1, 10) for x in range(6)]
        diagnostics = Diagnostics()
        grid = build_grid(rectangles, GridOptions(px=100, py=100, nx=3, ny=100),
                          diagnostics=diagnostics)
        assert not grid.cells
        assert "grid.forced_lines_overflow" in diagnostics.codes()
