"""Execution-local numerical backend, independent of the parser product."""
from contextlib import contextmanager
from contextvars import ContextVar

_backend = ContextVar("pcbeq_numerical_backend", default=None)


def current():
    return _backend.get()


@contextmanager
def use(backend):
    token = _backend.set(backend)
    try:
        yield
    finally:
        _backend.reset(token)


def rings(region):
    return [(r.points, r.is_hole) for r in region.rings]


def resource_limits():
    native = current()
    if native is None:
        return {}
    return {"max_region_axis_lines": native.MAX_AXIS_LINES,
            "max_grid_cells": native.MAX_GRID_CELLS,
            "max_raster_pixels": native.MAX_RASTER_PIXELS}
