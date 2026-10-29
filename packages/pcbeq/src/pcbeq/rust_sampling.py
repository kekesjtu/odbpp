"""Map typed Python IO records to the native raster/integration boundary."""
from .diagnostics import GRID_CELL_WITHOUT_PIXEL
from .raster import region_edges


def evaluate_layer(native, name, type_name, domain, black, z_start, z_end,
                   thickness_known, materials, grid, x_axis, y_axis, diagnostics,
                   emit, *, mask=None, step_domain=None, sampling_plan=None):
    from .equiv import EquivalentCell, _sampling_plan
    axis = lambda a: (a.origin, a.step, a.count)
    pixels = native.rasterize(region_edges(domain),
        region_edges(step_domain) if step_domain is not None else None,
        [region_edges(r) for r in black if r], axis(x_axis), axis(y_axis))
    if mask is not None:
        mask.pixels[:] = pixels
    if sampling_plan is None:
        sampling_plan = _sampling_plan(grid, x_axis, y_axis)
    samples = [(cell, a0, a1, b0, b1)
               for b0, b1, cells, _, _ in sampling_plan for cell, a0, a1 in cells]
    minimum = (grid.mesh_configuration or {}).get("minimum_samples_per_axis", 1)
    black, white = materials.black, materials.white
    values = native.integrate(pixels, x_axis.count, y_axis.count,
        [(a0, a1, b0, b1) for _, a0, a1, b0, b1 in samples],
        [materials.k1, materials.k2, black.density_kg_m3, white.density_kg_m3,
         black.specific_heat_j_kgk, white.specific_heat_j_kgk], minimum)
    emitted = no_pixel = 0
    for (cell, a0, a1, b0, b1), value in zip(samples, values):
        cols, rows = a1-a0, b1-b0
        if value is None:
            no_pixel += 1
            if no_pixel <= 20:
                diagnostics.error(GRID_CELL_WITHOUT_PIXEL,
                    f"layer {name!r} region {cell.region_id} cell ({cell.i},{cell.j}) "
                    f"bounds={cell.bounds}: samples={cols}x{rows}, required per axis={minimum}; "
                    "insufficient raster samples or domain/geometry contradiction; increase px/py; geometry is unchanged")
            continue
        phi, kx, ky, kz, density, cp = value
        emit(EquivalentCell(name, type_name, cell.region_id, cell.i, cell.j,
            cell.rect.x0, cell.rect.y0, cell.rect.x1, cell.rect.y1, z_start, z_end,
            thickness_known, phi, kx, ky, kz, density, cp,
            rows, cols, rows*cols, cell.rect.area, cell.rect.area, 1.0, False))
        emitted += 1
    if no_pixel > 20:
        diagnostics.error(GRID_CELL_WITHOUT_PIXEL,
            f"layer {name!r}: {no_pixel} cells have insufficient sampling; first 20 locations reported")
    return emitted, 0, no_pixel
