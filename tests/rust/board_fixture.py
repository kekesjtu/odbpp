"""Minimal JSON fixtures for the independent Rust products."""

def rect(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": True,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

def hole(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": False,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

def build_document(*, profile=None, layers=(), domains=None, z=0.035) -> dict:
    """A minimal but valid ``current intermediate`` document."""
    profile = profile if profile is not None else rect(0, 0, 10, 10)
    domain_list = domains or [{
        "id": "step_profile", "source": "step_profile",         "polygons": [profile],
    }]
    layer_docs = []
    for spec in layers:
        extent = spec.get("z_mm")
        if extent is None:
            extent = {"start_mm": 0.0, "end_mm": z, "thickness_known": True}
        row = {
            "name": spec["name"],
            "type": spec.get("type", "SIGNAL"),
            "polygons": spec.get("polygons", []),
            "domain_id": spec.get("domain_id", "step_profile"),
        }
        if row["type"] != "DRILL":
            row["z_mm"] = extent
        for field in ("copper_index", "copper_span"):
            if field in spec:
                row[field] = spec[field]
        layer_docs.append(row)
    return {"units": "mm",
            "steps": [{"name": "pcb", "domains": domain_list,
                       "layers": layer_docs, "profile_domain_id": "step_profile"}]}

def drill_document(**kwargs):
    """Explicit endpoint copper catalogue for synthetic dielectric/drill tests."""
    document = build_document(**kwargs)
    layers = document["steps"][0]["layers"]
    for layer in layers:
        if layer["type"] in ("DIELECTRIC", "DRILL"):
            layer["copper_span"] = [0, 1]
    layers[:0] = [
        {"name": name, "type": "SIGNAL", "polygons": [],
         "domain_id": "step_profile", "copper_index": index,
         "z_mm": {"start_mm": 0.0, "end_mm": 0.035, "thickness_known": True}}
        for index, name in enumerate(("ENDPOINT_TOP", "ENDPOINT_BOTTOM"))]
    return document
