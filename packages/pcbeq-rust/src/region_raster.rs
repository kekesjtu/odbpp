//! Pixel-center sampling on the uniform mesh of each region.
use crate::{
    Result,
    raster::{Axis, MAX_RASTER_PIXELS},
    sampling::Window,
};
use std::num::NonZeroUsize;

pub struct RegionRaster {
    pub x: Axis,
    pub y: Axis,
    pub windows: Vec<Window>,
}

pub fn build(
    axes: &[(Vec<f64>, Vec<f64>)],
    pixels_per_cell_axis: NonZeroUsize,
) -> Result<Vec<RegionRaster>> {
    let n = pixels_per_cell_axis.get();
    let mut rasters = Vec::with_capacity(axes.len());
    for (rid, (xs, ys)) in axes.iter().enumerate() {
        let (nx, ny) = (xs.len() - 1, ys.len() - 1);
        if nx
            .checked_mul(ny)
            .and_then(|cells| cells.checked_mul(n))
            .and_then(|rows| rows.checked_mul(n))
            .is_none_or(|count| count > MAX_RASTER_PIXELS)
        {
            return Err(format!(
                "region {rid}: {nx}x{ny} cells at {n}x{n} pixels per cell exceeds the raster limit of {MAX_RASTER_PIXELS} pixels"
            ));
        }
        let (width, height) = (nx * n, ny * n);
        let mut windows = Vec::with_capacity(nx * ny);
        for bottom in (0..height).step_by(n) {
            for left in (0..width).step_by(n) {
                windows.push((left, left + n, bottom, bottom + n));
            }
        }
        rasters.push(RegionRaster {
            x: (xs[0], (xs[nx] - xs[0]) / width as f64, width),
            y: (ys[0], (ys[ny] - ys[0]) / height as f64, height),
            windows,
        });
    }
    Ok(rasters)
}
