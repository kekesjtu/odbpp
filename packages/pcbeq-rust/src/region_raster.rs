//! Independent physical sampling windows; explicit density retains the board lattice.
use crate::{
    Result,
    options::Options,
    raster::{self, Axis},
};

pub struct RegionRaster {
    pub x: Axis,
    pub y: Axis,
    pub windows: Vec<(usize, usize, usize, usize)>,
}

pub fn build(
    bounds: [f64; 4],
    axes: &[(Vec<f64>, Vec<f64>)],
    minimum: usize,
    options: &Options,
) -> Result<Vec<RegionRaster>> {
    let spans = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
    let automatic = options.px.is_none();
    let base = if let (Some(x), Some(y)) = (options.px, options.py) {
        [x, y]
    } else {
        std::array::from_fn(|i| {
            ((4096. * spans[i] / spans[0].max(spans[1])).ceil() as usize).max(1)
        })
    };
    let reference: [Axis; 2] =
        std::array::from_fn(|i| (bounds[i], spans[i] / base[i] as f64, base[i]));
    let mut rasters = vec![];
    for (rid, (xs, ys)) in axes.iter().enumerate() {
        let mut local = [(0., 1., 1); 2];
        let mut indices = [vec![], vec![]];
        for (i, lines) in [xs, ys].into_iter().enumerate() {
            let n = lines.len() - 1;
            let lo = lines[0];
            let hi = lines[n];
            if automatic {
                let per_cell = ((hi - lo) / n as f64 / reference[i].1).ceil() as usize;
                let per_cell = per_cell.max(minimum + 1);
                let count = n
                    .checked_mul(per_cell)
                    .ok_or("region raster resource limit")?;
                local[i] = (lo, (hi - lo) / count as f64, count);
                indices[i] = (0..=n).map(|j| j * per_cell).collect();
            } else {
                // Extend the reference lattice beyond the board bounding box.
                // Keep signed offsets until shifting to this region's origin.
                let lattice_index =
                    |v: f64| ((v - reference[i].0) / reference[i].1 - 0.5 - 1e-9).ceil();
                let start = lattice_index(lo);
                let end = lattice_index(hi);
                local[i] = (
                    reference[i].0 + start * reference[i].1,
                    reference[i].1,
                    (end - start) as usize,
                );
                indices[i] = lines
                    .iter()
                    .map(|&v| (lattice_index(v) - start) as usize)
                    .collect();
            }
        }
        local[0]
            .2
            .checked_mul(local[1].2)
            .filter(|n| *n <= raster::MAX_RASTER_PIXELS)
            .ok_or(format!(
                "region {rid}: raster {}x{} exceeds {} pixels per region",
                local[0].2,
                local[1].2,
                raster::MAX_RASTER_PIXELS
            ))?;
        let mut windows = vec![];
        for (j, y) in indices[1].windows(2).enumerate() {
            for (i, x) in indices[0].windows(2).enumerate() {
                if x[1] - x[0] < minimum || y[1] - y[0] < minimum {
                    return Err(format!(
                        "region {rid} cell ({i},{j}): insufficient raster samples {}x{}, required {minimum} per axis; increase px/py; geometry is unchanged",
                        x[1] - x[0],
                        y[1] - y[0]
                    ));
                }
                windows.push((x[0], x[1], y[0], y[1]));
            }
        }
        rasters.push(RegionRaster {
            x: local[0],
            y: local[1],
            windows,
        });
    }
    Ok(rasters)
}
