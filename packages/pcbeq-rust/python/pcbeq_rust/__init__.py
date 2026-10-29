"""Rust numerical execution with pcbeq's Python reports and charts."""
from pcbeq.numerical import use
from . import _pcbeq_native

__version__ = "0.1.0"


def backend():
    return use(_pcbeq_native)
