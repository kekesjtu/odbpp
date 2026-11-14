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


def layer_profile_region(step, layer, options=None):
    """Resolve the authored extent with inherited step holes, retaining outer islands."""
    opts = options or TessellationOptions()
    profile = step.base_geometry(layer)
    board = step.profile if layer.profile is not None else None
    return profile.layer_region(board, opts.sagitta_mm, opts.max_segments)
