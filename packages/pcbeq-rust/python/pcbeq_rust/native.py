"""Launch the complete native file pipeline; only small option/result objects cross Python."""
import json
import os
from pathlib import Path
import sysconfig

from . import _pcbeq_native


def geos_library():
    configured = os.environ.get("PCBEQ_GEOS_LIBRARY")
    if configured:
        return str(Path(configured).resolve())
    site = Path(sysconfig.get_paths()["purelib"])
    for directory in (site / "shapely.libs", site / "shapely" / ".dylibs", site / "shapely"):
        for pattern in ("geos_c*.dll", "libgeos_c*.so*", "libgeos_c*.dylib"):
            candidates = sorted(directory.glob(pattern))
            if candidates:
                return str(candidates[0].resolve())
    raise RuntimeError("GEOS C library not found; install the Shapely wheel or set PCBEQ_GEOS_LIBRARY")


def run(source, output, **options):
    options["geos_library"] = geos_library()
    return json.loads(_pcbeq_native.run_materialmap(str(source), str(output), json.dumps(options)))
