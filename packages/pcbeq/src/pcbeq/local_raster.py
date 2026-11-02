"""Region-local sampling plans in the board's millimetre coordinate system."""
from dataclasses import dataclass, replace
import math

from .raster import RasterAxis

MAX_REGION_PIXELS = 120_000_000


@dataclass(frozen=True)
class RegionRaster:
    region_id: int
    x: RasterAxis
    y: RasterAxis
    samples_per_cell: tuple[int, int] | None
    offsets: tuple[int, int] = (0, 0)

    def as_dict(self):
        return {"region_id": self.region_id, "px": self.x.count, "py": self.y.count,
                "origin_mm": [self.x.origin, self.y.origin],
                "pixel_step_mm": [self.x.step, self.y.step]}


def plan_rasters(bounds, grid, options, config):
    spans = (bounds[2]-bounds[0], bounds[3]-bounds[1])
    counts = (tuple(max(1, math.ceil(4096*s/max(spans))) for s in spans)
              if options.auto_raster else (options.px, options.py))
    base = tuple(RasterAxis(origin, length/count, count)
                 for origin, length, count in zip(bounds[:2], spans, counts))
    tiles = []
    for region in grid.regions:
        axes, offsets, samples = [], [], []
        for lo, hi, n, reference in zip(region.rect.bounds[:2], region.rect.bounds[2:],
                                       (region.nx, region.ny), base):
            if options.auto_raster:
                per_cell = max(config["minimum_samples_per_axis"]+1,
                               math.ceil((hi-lo)/n/reference.step))
                count = n*per_cell
                axes.append(RasterAxis(lo, (hi-lo)/count, count))
                offsets.append(0)
                samples.append(per_cell)
            else:
                a, b = reference.range_for(lo, hi)
                # A zero-sample tile is retained for a located sampling error.
                axes.append(RasterAxis(reference.origin+a*reference.step,
                                       reference.step, max(1, b-a)))
                offsets.append(a)
                samples.append(b-a)
        actual_counts = ([a.count for a in axes] if options.auto_raster else samples)
        if math.prod(actual_counts) > MAX_REGION_PIXELS:
            raise ValueError(f"region {region.region_id}: raster needs {actual_counts[0]}x{actual_counts[1]} "
                             f"pixels; exceeds {MAX_REGION_PIXELS} pixels per region")
        tiles.append(RegionRaster(region.region_id, *axes,
                     tuple(samples) if options.auto_raster else None, tuple(offsets)))
    report = {"layout": "region-local", "mode": "automatic" if options.auto_raster else "explicit",
              "px": counts[0], "py": counts[1], "base_pixel_step_mm": [a.step for a in base],
              "default_long_side_px": 4096, "max_region_pixels": MAX_REGION_PIXELS,
              "regions": [t.as_dict() for t in tiles]}
    areas = [t.x.count*t.y.count for t in tiles]
    report.update(total_pixels=sum(areas), peak_region_pixels=max(areas, default=0))
    config["raster"] = report
    return counts, base, tiles


def domain_plans(grid, tiles, members, base):
    from .equiv import _sampling_plan
    cells = {}
    for cell in grid.cells:
        cells.setdefault(cell.region_id, []).append(cell)
    plans = []
    for tile in tiles:
        if tile.region_id not in members:
            continue
        local = replace(grid, cells=cells[tile.region_id])
        if tile.samples_per_cell is None:
            ox, oy = tile.offsets
            plan = [(b0-oy, b1-oy, [(c, a0-ox, a1-ox) for c, a0, a1 in row], xlo-ox, xhi-ox)
                    for b0, b1, row, xlo, xhi in _sampling_plan(local, *base)]
        else:
            sx, sy = tile.samples_per_cell
            rows = {}
            for c in local.cells:
                rows.setdefault(c.j, []).append((c, c.i*sx, (c.i+1)*sx))
            plan = [(j*sy, (j+1)*sy, row, 0, tile.x.count) for j, row in rows.items()]
        plans.append((local, tile, plan))
    return plans


def preview_axes(base):
    # Display sampling is bounded independently of numerical region sampling.
    factor = min(1., 1400/max(a.count for a in base))
    return tuple(RasterAxis(a.origin, a.step*a.count/max(1, round(a.count*factor)),
                            max(1, round(a.count*factor))) for a in base)


def evaluate_regions(name, type_name, domain, black, z_start, z_end, thickness_known,
                     materials, plans, diagnostics, emit, *, step_domain, base, mask_sink):
    from .equiv import _evaluate_layer
    from .scanline import PreparedRaster
    source = PreparedRaster(domain, black, step_domain)
    totals = [0, 0, 0]
    for grid, tile, plan in plans:
        if plan and (plan[-1][1] <= plan[0][0] or plan[0][4] <= plan[0][3]):
            from .diagnostics import GRID_CELL_WITHOUT_PIXEL
            diagnostics.error(GRID_CELL_WITHOUT_PIXEL,
                f"layer {name!r} region {tile.region_id}: no raster samples on an axis; "
                "increase px/py; geometry is unchanged")
            totals[2] += len(grid.cells)
            continue
        counts = _evaluate_layer(name, type_name, domain, black, z_start, z_end,
                    thickness_known, materials, grid, tile.x, tile.y, diagnostics, emit,
                    step_domain=step_domain, sampling_plan=plan, raster_source=source)
        totals = [a+b for a, b in zip(totals, counts)]
    if mask_sink is not None:
        x, y = preview_axes(base)
        mask_sink(name, source.render(x, y), (x.step, y.step))
    return totals
