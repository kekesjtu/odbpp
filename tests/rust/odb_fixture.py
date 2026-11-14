"""Build small ODB++ inputs in code for parser acceptance tests."""


def profile_text(polygons, units="MM"):
    lines = [f"UNITS={units}", "F 1", "S P 0"]
    for polygon in polygons:
        points = list(reversed(polygon["points"])) if polygon["island"] else polygon["points"]
        start, *rest = points
        lines.append(f"OB {start[0]} {start[1]} {'I' if polygon['island'] else 'H'}")
        lines.extend(f"OS {x} {y}" for x, y in [*rest, start])
        lines.append("OE")
    return "\n".join([*lines, "SE", ""])


def odb_files(layers, *, units="MM"):
    """Return job files; each layer supplies matrix fields and authored file text."""
    from board_fixture import rect

    scope = units or "INCH"
    files = {
        "misc/info": "JOB_NAME=parser-test\nODB_VERSION_MAJOR=8\nODB_VERSION_MINOR=1\n"
        + (f"UNITS={units}\n" if units else ""),
        "steps/board/stephdr": f"UNITS={scope}\nX_DATUM=0\nY_DATUM=0\nX_ORIGIN=0\nY_ORIGIN=0\n",
        "steps/board/profile": profile_text([rect(0, 0, 10, 10)], scope),
    }
    matrix = ["STEP {\nCOL=1\nNAME=BOARD\n}"]
    for row, layer in enumerate(layers, 1):
        fields = {
            "ROW": row, "NAME": layer["name"], "CONTEXT": "BOARD",
            "TYPE": layer.get("type", "SIGNAL"), "POLARITY": "POSITIVE", "FORM": "RIGID",
            **layer.get("matrix", {}),
        }
        matrix.append("LAYER {\n" + "\n".join(
            f"{key}={value}" for key, value in fields.items() if value is not None
        ) + "\n}")
        prefix = f"steps/board/layers/{layer['name'].lower()}"
        files[prefix + "/features"] = layer.get("features", f"UNITS={scope}\nF 0\n")
        for filename in ("attrlist", "profile", "tools"):
            if filename in layer:
                files[prefix + "/" + filename] = layer[filename]
    files["matrix/matrix"] = "\n".join(matrix) + "\n"
    return files


def exported_layers(job):
    from pcbkit_rust.export import build_step_preview, step_to_json

    step = step_to_json(job, build_step_preview(job))["steps"][0]
    return {layer["name"]: layer for layer in step["layers"]}
