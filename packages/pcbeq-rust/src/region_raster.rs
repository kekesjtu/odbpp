//! Independent physical sampling windows; explicit density retains the board lattice.
use crate::{
    geos::Result,
    options::Options,
    raster::{self, Axis},
};
use serde_json::{Value, json};

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
) -> Result<([usize; 2], Vec<RegionRaster>, Value)> {
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
    let mut reports = vec![];
    let mut total = 0usize;
    let mut peak = 0;
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
                let start = raster::pixel_index(lo, reference[i]);
                let end = raster::pixel_index(hi, reference[i]);
                local[i] = (
                    reference[i].0 + start as f64 * reference[i].1,
                    reference[i].1,
                    end - start,
                );
                indices[i] = lines
                    .iter()
                    .map(|&v| raster::pixel_index(v, reference[i]) - start)
                    .collect();
            }
        }
        let pixels = local[0]
            .2
            .checked_mul(local[1].2)
            .filter(|n| *n <= raster::MAX_RASTER_PIXELS)
            .ok_or(format!(
                "region {rid}: raster {}x{} exceeds {} pixels per region",
                local[0].2,
                local[1].2,
                raster::MAX_RASTER_PIXELS
            ))?;
        total = total
            .checked_add(pixels)
            .ok_or("region raster pixel count overflow")?;
        peak = peak.max(pixels);
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
        reports.push(json!({"region_id":rid,"px":local[0].2,"py":local[1].2,
            "origin_mm":[local[0].0,local[1].0],"pixel_step_mm":[local[0].1,local[1].1]}));
        rasters.push(RegionRaster {
            x: local[0],
            y: local[1],
            windows,
        });
    }
    let report = json!({"layout":"region-local","mode":if automatic{"automatic"}else{"explicit"},
        "px":base[0],"py":base[1],"base_pixel_step_mm":[reference[0].1,reference[1].1],
        "default_long_side_px":4096,"max_region_pixels":raster::MAX_RASTER_PIXELS,
        "total_pixels":total,"peak_region_pixels":peak,"regions":reports});
    Ok((base, rasters, report))
}
