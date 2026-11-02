use crate::{
    document::Step,
    geos::{Geos, Result},
    grid,
    options::Options,
    planning::Geometry,
};
use serde_json::{Value, json};
pub struct Mesh {
    pub axes: Vec<(Vec<f64>, Vec<f64>)>,
    pub pixels: [usize; 2],
    pub cell_count: usize,
    pub minimum: usize,
    pub config: Value,
}
pub fn build(engine: &Geos, step: &Step, geometry: &Geometry, options: &Options) -> Result<Mesh> {
    let bounds = geometry.bounds;
    let spans = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
    let mut maximum = options.maximum();
    let mut minimum = 1;
    let mut config = json!({"mode":"expert","minimum_samples_per_axis":1});
    if let Some(preset) = options.grid_level.as_deref() {
        let mut estimates = vec![];
        for layer in step.layers.iter().filter(|l| l.copper()) {
            for ring in layer.polygons.iter().filter(|p| p.island) {
                let polygon = engine.polygon(&ring.points, &[])?;
                if !polygon.empty()? && polygon.valid()? && polygon.length()? > 0. {
                    estimates.push(2. * polygon.area()? / polygon.length()?);
                }
            }
        }
        let mut usable = estimates
            .iter()
            .copied()
            .filter(|v| *v >= 0.01)
            .collect::<Vec<_>>();
        usable.sort_by(f64::total_cmp);
        let representative = if usable.is_empty() {
            None
        } else {
            Some(usable[((usable.len() - 1) as f64 * 0.1) as usize])
        };
        let (divisions, factor) = match preset {
            "coarse" => (32., 24.),
            "fine" => (128., 6.),
            _ => (64., 12.),
        };
        let mut target = (spans[0] * spans[1]).sqrt() / divisions;
        if let Some(width) = representative {
            target = target.min(width * factor);
        }
        target = target.max(spans[0].max(spans[1]) / 512.);
        maximum = [target, target];
        minimum = 8;
        config = json!({"mode":"preset","preset":preset,"minimum_samples_per_axis":8,
            "feature_estimator":"positive copper polygon 2A/P, 10th percentile >= 0.01 mm",
            "feature_estimate_min_mm":estimates.iter().copied().reduce(f64::min),
            "feature_estimate_representative_mm":representative,"feature_estimate_count":estimates.len(),
            "ignored_sliver_estimate_count":estimates.len()-usable.len(),"board_scale_divisions":divisions,
            "feature_multiplier":factor,"target_max_cell_mm":target});
    }
    config["max_cell_mm"] = json!(maximum);
    let mut axes = vec![];
    let mut cell_count = 0usize;
    let mut widths = [f64::INFINITY; 2];
    for (i, r) in geometry.rectangles.iter().enumerate() {
        let x = grid::axis(r[0], r[2], maximum[0]).map_err(|e| format!("X region {i}: {e}"))?;
        let y = grid::axis(r[1], r[3], maximum[1]).map_err(|e| format!("Y region {i}: {e}"))?;
        cell_count = cell_count
            .checked_add(
                (x.len() - 1)
                    .checked_mul(y.len() - 1)
                    .ok_or("grid cell resource limit")?,
            )
            .ok_or("grid cell resource limit")?;
        if cell_count > grid::MAX_GRID_CELLS {
            return Err("grid cell resource limit: 5000000".into());
        }
        widths[0] = widths[0].min(
            x.windows(2)
                .map(|p| p[1] - p[0])
                .fold(f64::INFINITY, f64::min),
        );
        widths[1] = widths[1].min(
            y.windows(2)
                .map(|p| p[1] - p[0])
                .fold(f64::INFINITY, f64::min),
        );
        axes.push((x, y));
    }
    if cell_count == 0 {
        return Err("no modeled board cells".into());
    }
    let pixels = if let (Some(x), Some(y)) = (options.px, options.py) {
        [x, y]
    } else {
        std::array::from_fn(|i| {
            ((4096. * spans[i] / spans[0].max(spans[1])).ceil() as usize)
                .max((spans[i] * (minimum + 1) as f64 / widths[i]).ceil() as usize)
                .max(1)
        })
    };
    if pixels[0]
        .checked_mul(pixels[1])
        .is_none_or(|n| n > 120_000_000)
    {
        return Err(format!(
            "raster resource limit: {}x{} exceeds 120000000 pixels",
            pixels[0], pixels[1]
        ));
    }
    config["raster"] = json!({"mode":if options.px.is_some(){"explicit"}else{"automatic"},"px":pixels[0],"py":pixels[1],
        "default_long_side_px":4096,"max_default_pixels":120000000});
    Ok(Mesh {
        axes,
        pixels,
        cell_count,
        minimum,
        config,
    })
}
