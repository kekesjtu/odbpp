"""Make audited, quiet ODB++ ZIPs from the untouched native KiCad exports.

Only the matrix and archive membership change. Profile, features, attrlist and
drill tools are copied byte-for-byte. The source ZIP remains in native/.
"""

from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
KEEP_TYPES = {"SIGNAL", "DIELECTRIC", "DRILL"}
MATRIX_LAYER = re.compile(r"LAYER\s*\{[^}]*\}", re.MULTILINE)


def field(block: str, name: str) -> str:
    found = re.search(rf"^\s*{re.escape(name)}=(.*)$", block, re.MULTILINE)
    if not found:
        raise ValueError(f"missing matrix {name}: {block}")
    return found.group(1).strip()


def check_features(data: bytes, expected: int | None = None) -> int:
    match = re.search(rb"(?m)^F (\d+)\s*$", data)
    if not match:
        raise ValueError("features lacks F count")
    count = int(match.group(1))
    if expected is not None and count != expected:
        raise ValueError(f"expected {expected} features, got {count}")
    return count


def curate(source: Path, target: Path) -> None:
    with ZipFile(source) as original:
        names = set(original.namelist())
        info = original.read("misc/info").decode()
        for marker in ("ODB_VERSION_MAJOR=8", "ODB_VERSION_MINOR=1",
                       "ODB_SOURCE=KiCad EDA"):
            if marker not in info:
                raise ValueError(f"{source}: missing {marker}")
        profile = original.read("steps/pcb/profile")
        check_features(profile, 1)
        matrix = original.read("matrix/matrix").decode()
        blocks = MATRIX_LAYER.findall(matrix)
        if not blocks:
            raise ValueError(f"{source}: no matrix layers")
        kept: list[str] = []
        kept_dirs: set[str] = set()
        dropped: list[str] = []
        for block in blocks:
            layer_name = field(block, "NAME")
            layer_type = field(block, "TYPE")
            directory = f"steps/pcb/layers/{layer_name.lower()}/"
            features = directory + "features"
            if features not in names:
                raise ValueError(f"{source}: matrix layer lacks {features}")
            count = check_features(original.read(features))
            if layer_type in KEEP_TYPES:
                if field(block, "POLARITY") != "POSITIVE":
                    raise ValueError(f"{source}: unexpected negative layer")
                if layer_type == "DRILL":
                    field(block, "START_NAME")
                    field(block, "END_NAME")
                    if directory + "tools" not in names:
                        raise ValueError(f"{source}: missing drill tools")
                kept_dirs.add(directory)
                kept.append(re.sub(r"(?m)^\s*ROW=\d+", f"    ROW={len(kept)+1}", block))
            else:
                # KiCad emits four Edge.Cuts lines from the board source,
                # duplicated by the canonical step profile. Other dropped
                # component/document layers must be empty.
                if count != (4 if layer_name == "EDGE.CUTS" else 0):
                    raise ValueError(f"{source}: nonempty unexpected layer {layer_name}")
                dropped.append(layer_name)
        if "EDGE.CUTS" not in dropped:
            raise ValueError(f"{source}: expected native Edge.Cuts noise")
        first_layer = matrix.index(blocks[0])
        new_matrix = (matrix[:first_layer].rstrip() + "\n\n" +
                      "\n\n".join(kept) + "\n").encode()
        wanted = {"matrix/matrix", "misc/info", "steps/pcb/stephdr",
                  "steps/pcb/profile"}
        wanted |= {name for name in names if any(name.startswith(d) for d in kept_dirs)
                   and not name.endswith("/")}
        # Do not silently omit a newly introduced payload such as symbols/.
        extra = [name for name in names if name.startswith("symbols/")
                 and not name.endswith("/")]
        if extra:
            raise ValueError(f"{source}: custom symbols require explicit review: {extra}")
        target.parent.mkdir(exist_ok=True)
        with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as clean:
            for name in sorted(wanted):
                clean.writestr(name, new_matrix if name == "matrix/matrix"
                               else original.read(name))
    print(f"{source.name} -> {target.name}: kept {len(kept)} physical layers; "
          f"dropped {', '.join(dropped)}")


def main() -> None:
    for source in sorted((ROOT / "native").glob("*.zip")):
        curate(source, ROOT / "clean" / source.name)


if __name__ == "__main__":
    main()
