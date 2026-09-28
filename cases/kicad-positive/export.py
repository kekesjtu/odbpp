"""Re-export the fixed source boards with the native KiCad ODB++ CLI.

Run with the project's uv Python. This script does not import pcbnew and does
not modify the returned ZIP files. ``curate.py`` makes the separate clean set.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
EXPORTS = (
    ("outline-only", "mm"),
    ("pad-shapes", "mm"),
    ("tracks-arcs", "mm"),
    ("through-hole", "mm"),
    ("through-hole", "in"),
    ("zone-hole-rotation", "mm"),
    ("four-layer-drills", "mm"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kicad-cli", required=True, type=Path)
    args = parser.parse_args()
    native = ROOT / "native"
    native.mkdir(exist_ok=True)
    for name, units in EXPORTS:
        source = ROOT / "sources" / f"{name}.kicad_pcb"
        target = native / f"{name}-{units}.zip"
        command = [str(args.kicad_cli), "pcb", "export", "odb", "--units",
                   units, "--precision", "6", "--compression", "zip",
                   "-o", str(target), str(source)]
        subprocess.run(command, check=True)
        print(target)


if __name__ == "__main__":
    main()
