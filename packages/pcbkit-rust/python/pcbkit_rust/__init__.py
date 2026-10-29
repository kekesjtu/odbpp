"""Rust numerical execution with pcbkit's Python IO and visualization."""
from pcbkit.numerical import use
from . import _pcbkit_native

__version__ = "0.1.0"


def backend():
    """Scope pcbkit numerical calls to Rust; safe for nested invocations."""
    return use(_pcbkit_native)
