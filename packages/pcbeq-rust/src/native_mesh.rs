use crate::{Result, grid, options::Options, planning::Geometry};
pub struct Mesh {
    pub axes: Vec<(Vec<f64>, Vec<f64>)>,
    pub rasters: Vec<crate::region_raster::RegionRaster>,
}
pub fn build(geometry: &Geometry, options: &Options) -> Result<Mesh> {
    let maximum = if let Some(preset) = options.grid_level.as_deref() {
        let [x0, y0, x1, y1] = geometry.bounds;
        let (width, height) = (x1 - x0, y1 - y0);
        let divisions = match preset {
            "coarse" => 32.,
            "fine" => 128.,
            _ => 64.,
        };
        let target = ((width * height).sqrt() / divisions).max(width.max(height) / 512.);
        [target; 2]
    } else {
        options.maximum()
    };
    let mut axes = vec![];
    let mut cell_count = 0usize;
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
        axes.push((x, y));
    }
    if cell_count == 0 {
        return Err("no modeled board cells".into());
    }
    let rasters = crate::region_raster::build(&axes, options.pixels_per_cell_axis)?;
    Ok(Mesh { axes, rasters })
}
