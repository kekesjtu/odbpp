"""Opaque native geometry handles used at the preview boundary."""
from ._pcbkit_native import Region, Ring, region_boolean
from .geometry import TessellationOptions


def union(*regions):
    return region_boolean("union", regions)


def difference(subject, clip):
    return region_boolean("difference", (subject, clip))


def intersection(*regions):
    return region_boolean("intersection", regions)


def snap_to_grid(region, digits):
    return region.snap(digits)


def region_from_geometry(profile, options=None):
    opts = options or TessellationOptions()
    return profile.region(opts.sagitta_mm, opts.max_segments)
