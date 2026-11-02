"""Vector topology and protected physical cores, independent of raster and error budgets."""
from __future__ import annotations
import math
from .region_profile import _components

def _topology_source(original):
    from shapely.geometry import Polygon
    sources = _components(original)
    holes = [Polygon(h) for s in sources for h in s.interiors]
    cores = [(radius, _components(original.buffer(-radius)))
             for radius in (0.03125, 0.1, 0.25, 0.5, 1.0)]
    return sources, holes, cores


def _covers_with_roundoff(outer, inner):
    """Containment predicate only: allow coordinate roundoff, never move geometry.

    Raw line intersections can land a few binary ULPs from a shared boundary.
    This slack scales with floating-point precision, not a physical Y grid.
    """
    if outer.covers(inner):
        return True
    slack = 32 * max(math.ulp(v) for v in (*outer.bounds,*inner.bounds))
    return outer.buffer(slack,quad_segs=32).covers(inner)


def _topology(original, model, source=None):
    from shapely.geometry import Polygon
    sources, holes, eroded = source if source is not None else _topology_source(original)
    targets = _components(model)
    points = [t.representative_point() for t in targets]
    matches = [sum(s.covers(point) for point in points) for s in sources]
    model_holes = [Polygon(h) for t in targets for h in t.interiors]
    hole_matches = [sum(_covers_with_roundoff(m,h) for m in model_holes) for h in holes]
    positive = all(len(_components(t.buffer(-1e-7))) == 1 for t in targets)
    # Erosion splits lobes separated by a narrow neck. Preserve every such
    # geometric core, including terminal branches within a single source island.
    # These scales are physical millimetres, entirely independent of sampling.
    core_checks = []
    for radius, cores in eroded:
        overlaps = [c.intersection(model) for c in cores]
        kept = [not overlap.is_empty and overlap.area > 1e-10 for overlap in overlaps]
        core_checks.append({"erosion_mm":radius,"cores":len(cores),"retained":sum(kept)})
    core_preserved = all(c["cores"] == c["retained"] for c in core_checks)
    return {"original_components": len(sources), "model_components": len(targets),
            "component_correspondence": matches,
            "original_holes": len(holes), "model_holes": len(model_holes),
            "hole_correspondence": hole_matches, "positive_width_connections": positive,
            "hole_containment_roundoff_ulps":32,
            "protected_core_checks":core_checks,
            "preserved": all(n == 1 for n in matches + hole_matches)
                         and len(targets) == len(sources) and len(model_holes) == len(holes)
                         and positive and core_preserved}
