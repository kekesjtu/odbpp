//! FAST-style non-overlapping rectangle approximation: largest foreground seed,
//! area-weighted foreground/background benefit, then coordinate ascent of its edges.
use crate::{
    geos::{Result, Shape},
    rectangles::{self, Rect},
    topology::Source,
};
use serde_json::{Value, json};

const MAX_PIXELS: usize = 4_000_000;
const MAX_RECTANGLES: usize = 10_000;
type Window = [usize; 4];
pub struct Plan {
    pub rectangles: Vec<Rect>,
    pub audit: Value,
}
#[path = "fast_integral.rs"]
mod integral;
#[path = "fast_mask.rs"]
mod mask;
use integral::Integral;
use mask::Mask;

fn audit(
    original: &Shape<'_>,
    source: &Source<'_>,
    rectangles: &[Rect],
    ratio: f64,
) -> Result<Value> {
    let model = original.engine.rectangles(rectangles)?;
    let original_area = original.area()?;
    let lost = original.difference(&model)?.area()?;
    let gained = model.difference(original)?.area()?;
    let topology = source.topology(&model)?;
    Ok(
        json!({"ok":lost+gained<=ratio*original_area+1e-8 && topology["preserved"]==true,
        "strategy":"fast-greedy-rectangles","original_area_mm2":original_area,
        "rectified_area_mm2":model.area()?,"lost_area_mm2":lost,"gained_area_mm2":gained,
        "shape_deviation_mm2":lost+gained,"relative_area_error":(lost+gained)/original_area,
        "net_area_error_mm2":gained-lost,"iou":(original_area-lost)/(original_area+gained),
        "rectangle_count":rectangles.len(),"audit":{"topology":topology,
        "non_overlapping":true,"relative_area_budget":ratio,"area_budget_mm2":ratio*original_area}}),
    )
}

pub fn approximate(original: &Shape<'_>, ratio: f64) -> Result<Plan> {
    let source = Source::new(original)?;
    let bounds = original.bounds()?;
    if original.covers(&original.engine.rectangle(bounds)?)? {
        return Ok(Plan {
            rectangles: vec![bounds],
            audit: audit(original, &source, &[bounds], ratio)?,
        });
    }
    let allowed = original.area()? * ratio;
    let mut attempts = vec![];
    for resolution in [128, 256, 512, 1024] {
        for tau in [0.5, 0.75, 0.95] {
            let mut mask = Mask::new(original, resolution)?;
            let pixels = [mask.width(), mask.height()];
            let mut remaining = 0.;
            for row in 0..mask.height() {
                for col in 0..mask.width() {
                    if mask.foreground[row * mask.width() + col] {
                        remaining +=
                            (mask.x[col + 1] - mask.x[col]) * (mask.y[row + 1] - mask.y[row]);
                    }
                }
            }
            let mut gained = 0.;
            let mut rectangles = vec![];
            let mut last = json!({"reason":"empty search mask"});
            while let Some(seed) = mask.seed() {
                let integral = Integral::new(&mask, tau);
                let window = integral.optimize(&mask, seed);
                rectangles.push(mask.rectangle(window));
                let (covered, outside) = mask.cover(window);
                remaining = (remaining - covered).max(0.);
                gained += outside;
                if gained > allowed + 1e-8 || rectangles.len() > MAX_RECTANGLES {
                    break;
                }
                if remaining + gained <= allowed + 1e-8 {
                    let merged = rectangles::merge(rectangles.clone());
                    last = audit(original, &source, &merged, ratio)?;
                    if last["ok"] == true {
                        attempts.push(json!({"resolution":resolution,"tau":tau,"ok":true}));
                        last["audit"]["search"] = json!({"pixels":pixels,"resolution":resolution,
                        "tau":tau,"attempts":attempts,"max_rectangles":MAX_RECTANGLES,
                        "max_search_cells":MAX_PIXELS,"seed":"largest available foreground rectangle",
                        "benefit":"area*(foreground-tau)","edge_search":"coordinate ascent"});
                        return Ok(Plan {
                            rectangles: merged,
                            audit: last,
                        });
                    }
                    if last["shape_deviation_mm2"].as_f64().unwrap() > allowed + remaining + 1e-8 {
                        break;
                    }
                }
            }
            attempts.push(json!({"resolution":resolution,"tau":tau,"ok":false,
            "rectangle_count":rectangles.len(),"estimated_error_mm2":remaining+gained,"audit":last}));
        }
    }
    Err(format!(
        "FAST geometry cannot satisfy area/topology constraints for bounds={bounds:?}: {attempts:?}"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn weighted_seed_and_growth_use_physical_area_and_block_overlaps() {
        let mut mask = Mask {
            x: vec![0., 1., 10.],
            y: vec![0., 1., 2.],
            foreground: vec![true, false, true, true],
            blocked: vec![false; 4],
        };
        let seed = mask.seed().unwrap();
        assert_eq!(seed, [0, 1, 2, 2]);
        let grown = Integral::new(&mask, 0.5).optimize(&mask, seed);
        assert_eq!(grown, seed);
        mask.cover(grown);
        let next = mask.seed().unwrap();
        assert_eq!(
            Integral::new(&mask, 0.5).optimize(&mask, next),
            [0, 0, 1, 1]
        );
    }
    #[test]
    fn boundary_background_can_be_included_when_it_increases_benefit() {
        let mask = Mask {
            x: vec![0., 1., 2., 3.],
            y: vec![0., 1., 2., 3.],
            foreground: vec![false, true, true, true, true, true, true, true, true],
            blocked: vec![false; 9],
        };
        let result = Integral::new(&mask, 0.5).optimize(&mask, mask.seed().unwrap());
        assert_eq!(result, [0, 0, 3, 3]);
    }
}
