"""Input options for native curve discretisation."""
import math
from dataclasses import dataclass

DEFAULT_SAGITTA_MM = 0.005


@dataclass(frozen=True)
class TessellationOptions:
    sagitta_mm: float = DEFAULT_SAGITTA_MM
    max_segments: int = 4096

    def __post_init__(self):
        if not math.isfinite(self.sagitta_mm) or self.sagitta_mm <= 0:
            raise ValueError("sagitta_mm must be finite and positive")
        if not 1 <= self.max_segments <= 1_000_000:
            raise ValueError("max_segments must be in 1..1000000")
