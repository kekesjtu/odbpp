//! Uniform raster adapter for the original FAST rectangle-covering algorithm.
use crate::{
    document,
    geos::{Result, Shape},
    raster,
    rectangles::Rect,
    topology::Source,
};
use serde_json::{Value, json};
#[path = "fast_cover.rs"]
mod cover;
#[path = "fast_image.rs"]
mod image;

const LONG_SIDE_PIXELS: usize = 1024;
pub struct Plan {
    pub rectangles: Vec<Rect>,
    pub audit: Value,
}

pub fn approximate(original: &Shape<'_>, ratio: f64) -> Result<Plan> {
    let bounds = original.bounds()?;
    let span = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
    let longest = span[0].max(span[1]);
    let counts = span.map(|s| ((s / longest * LONG_SIDE_PIXELS as f64).round() as usize).max(1));
    let spacing = [span[0] / counts[0] as f64, span[1] / counts[1] as f64];
    let polygons = document::polygons(original)?;
    let edges = polygons
        .iter()
        .flat_map(|p| {
            p.points
                .iter()
                .zip(p.points.iter().cycle().skip(1))
                .take(p.points.len())
                .map(|(&(x, y), &(a, b))| (x, y, a, b))
        })
        .collect();
    let pixels = raster::rasterize_pixels(
        edges,
        None,
        vec![],
        (bounds[0], spacing[0], counts[0]),
        (bounds[1], spacing[1], counts[1]),
    )?;
    let image = image::Image {
        width: counts[0],
        height: counts[1],
        foreground: pixels.iter().map(|p| *p == 255).collect(),
    };
    let result = cover::cover(&image, ratio)?;
    let rectangles: Vec<Rect> = result
        .windows
        .iter()
        .map(|w| {
            let coordinate = |axis: usize, index: usize| {
                if index == counts[axis] {
                    bounds[axis + 2]
                } else {
                    bounds[axis] + index as f64 * spacing[axis]
                }
            };
            [
                coordinate(0, w[0]),
                coordinate(1, w[1]),
                coordinate(0, w[2]),
                coordinate(1, w[3]),
            ]
        })
        .collect();
    let model = original.engine.rectangles(&rectangles)?;
    let original_area = original.area()?;
    let lost = original.difference(&model)?.area()?;
    let gained = model.difference(original)?.area()?;
    let topology = Source::new(original)?.topology(&model)?;
    let audit = json!({"ok":true,"strategy":"fast-paper-section-3.1",
        "original_area_mm2":original_area,"rectified_area_mm2":model.area()?,
        "lost_area_mm2":lost,"gained_area_mm2":gained,"shape_deviation_mm2":lost+gained,
        "relative_area_error":(lost+gained)/original_area,"net_area_error_mm2":gained-lost,
        "iou":(original_area-lost)/(original_area+gained),"rectangle_count":rectangles.len(),
        "vector_area_budget_met":lost+gained<=ratio*original_area+1e-8,
        "audit":{"topology":topology,"topology_is_constraint":false,"non_overlapping":true,
            "relative_area_budget":ratio,"area_budget_mm2":ratio*original_area,
            "stopping_metric":"binary raster symmetric difference / original foreground pixels",
            "pixel_step_mm":spacing,"search":result.audit}});
    Ok(Plan { rectangles, audit })
}
