"""Geometry-only mesh presets and downstream raster capacity planning."""
from dataclasses import replace
import math
from .grid import size_pair

MIN_SAMPLES_PER_AXIS = 8
DEFAULT_RASTER_LONG_SIDE = 4096
MAX_DEFAULT_RASTER_PIXELS = 120_000_000

def resolve_mesh(step, rectangles, options):
    forced = [sorted({v for r in rectangles for v in (getattr(r,a+"0"),getattr(r,a+"1"))}) for a in "xy"]
    gaps = [min(b-a for a,b in zip(v,v[1:])) for v in forced]
    spans = [v[-1]-v[0] for v in forced]
    config = {"mode":"expert", "minimum_samples_per_axis":1}
    if options.mesh_preset:
        from shapely.geometry import Polygon
        estimates=[]
        for layer in step.get("layers",[]):
            if layer.get("type") not in ("SIGNAL","POWER_GROUND","MIXED"): continue
            for ring in layer.get("polygons",[]):
                if not ring.get("island",True): continue
                polygon=Polygon(ring.get("points",[]))
                if polygon.is_empty or not polygon.is_valid or polygon.length<=0: continue
                # 2A/P approximates width for long traces; it is not a measured line width.
                estimates.append(2*polygon.area/polygon.length)
        usable=sorted(v for v in estimates if v>=.01)
        representative=usable[int((len(usable)-1)*.1)] if usable else None
        divisions,factor={"coarse":(32,24),"standard":(64,12),"fine":(128,6)}[options.mesh_preset]
        target=math.sqrt(spans[0]*spans[1])/divisions
        if representative is not None: target=min(target,representative*factor)
        target=max(target,max(spans)/512)
        lows=tuple(min(g/2,target/4) for g in gaps)
        highs=(target,target)
        options=replace(options,min_cell_mm=lows,max_cell_mm=highs)
        config={"mode":"preset", "preset":options.mesh_preset,
                "minimum_samples_per_axis":MIN_SAMPLES_PER_AXIS,
                "feature_estimator":"positive copper polygon 2A/P, 10th percentile >= 0.01 mm",
                "feature_estimate_min_mm":min(estimates) if estimates else None,
                "feature_estimate_representative_mm":representative,
                "feature_estimate_count":len(estimates),
                "ignored_sliver_estimate_count":len(estimates)-len(usable),
                "board_scale_divisions":divisions,"feature_multiplier":factor,
                "target_max_cell_mm":target,"minimum_forced_interval_mm":gaps}
    config["min_cell_mm"]=list(options.min_cell_mm)
    config["max_cell_mm"]=list(options.max_cell_mm)
    return options,config

def resolve_raster(bounds, grid, options, config):
    spans=(bounds[2]-bounds[0],bounds[3]-bounds[1])
    widths=[min(getattr(c.rect,a+"1")-getattr(c.rect,a+"0") for c in grid.cells) for a in "xy"]
    threshold=config["minimum_samples_per_axis"]
    if options.auto_raster:
        # One extra sample per smallest interval protects arbitrary pixel phase.
        counts=tuple(max(1,math.ceil(DEFAULT_RASTER_LONG_SIDE*s/max(spans)),
                         math.ceil(s*(threshold+1)/w)) for s,w in zip(spans,widths))
        if counts[0]*counts[1]>MAX_DEFAULT_RASTER_PIXELS:
            raise ValueError(f"default raster needs {counts[0]}x{counts[1]} pixels; "
                             "exceeds 120 million pixel budget; use expert sizes or explicit --px/--py")
    else:
        counts=(options.px,options.py)
    config["raster"]={"mode":"automatic" if options.auto_raster else "explicit",
                       "px":counts[0],"py":counts[1],
                       "default_long_side_px":DEFAULT_RASTER_LONG_SIDE,
                       "max_default_pixels":MAX_DEFAULT_RASTER_PIXELS}
    return counts
