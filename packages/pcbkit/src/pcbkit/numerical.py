"""Execution-local numerical backend; rendering and IO stay in Python."""
from contextlib import contextmanager
from contextvars import ContextVar

_backend = ContextVar("pcbkit_numerical_backend", default=None)


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
