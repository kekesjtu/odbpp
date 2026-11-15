"""Minimal JSON fixtures for the independent Rust products."""

def rect(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": True,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

def hole(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": False,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

def build_document(*, profile=None, layers=(), profiles=None, z=0.035) -> dict:
    """A minimal but valid ``current intermediate`` document."""
    profile = profile if profile is not None else rect(0, 0, 10, 10)
    profile_list = profiles or [{
        "id": "step_profile",          "polygons": [profile],
    }]
    profile_list = [dict(p, layers=[]) for p in profile_list]
    layer_docs = []
    for position, spec in enumerate(layers, start=1):
        extent = spec.get("z_mm")
        if extent is None:
            extent = {"start_mm": 0.0, "thickness_mm": z}
        row = {
            "name": spec["name"],
            "row": spec.get("row", position),
            "type": spec.get("type", "SIGNAL"),
            "polygons": spec.get("polygons", []),
        }
        if row["type"] != "DRILL":
            row["z_mm"] = extent
        for field in ("start_layer", "end_layer"):
            if field in spec:
                row[field] = spec[field]
        next(p for p in profile_list if p["id"] == spec.get("profile_id", "step_profile"))["layers"].append(spec["name"])
        layer_docs.append(row)
    return {"units": "mm",
            "steps": [{"name": "pcb", "profiles": profile_list,
                       "layers": layer_docs, "profile_id": "step_profile"}]}

def drill_document(**kwargs):
    """Explicit endpoint copper catalogue for synthetic dielectric/drill tests."""
    document = build_document(**kwargs)
    layers = document["steps"][0]["layers"]
    for layer in layers:
        if layer["type"] == "DRILL":
            layer.update(start_layer="ENDPOINT_TOP", end_layer="ENDPOINT_BOTTOM")
    endpoints = [
        {"name": name, "type": "SIGNAL", "polygons": [],
         "z_mm": {"start_mm": 0.0, "thickness_mm": 0.035}}
        for name in ("ENDPOINT_TOP", "ENDPOINT_BOTTOM")]
    layers.insert(0, endpoints[0])
    layers.append(endpoints[1])
    for position, layer in enumerate(layers, start=1):
        layer["row"] = position
    document["steps"][0]["profiles"][0]["layers"].extend(["ENDPOINT_TOP", "ENDPOINT_BOTTOM"])
    return document
