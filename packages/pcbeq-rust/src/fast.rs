//! FAST-style non-overlapping rectangle approximation: largest foreground seed,
//! area-weighted foreground/background benefit, then coordinate ascent of its edges.
use crate::{
    document,
    geos::{Result, Shape},
    rectangles::{self, Rect, area, unique},
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
struct Mask {
    x: Vec<f64>,
    y: Vec<f64>,
    foreground: Vec<bool>,
    blocked: Vec<bool>,
}

impl Mask {
    fn new(original: &Shape<'_>, resolution: usize) -> Result<Self> {
        let polygons = document::polygons(original)?;
        let bounds = original.bounds()?;
        let orthogonal = polygons.iter().all(|p| {
            p.points
                .iter()
                .zip(p.points.iter().cycle().skip(1))
                .all(|(a, b)| a.0 == b.0 || a.1 == b.1)
        });
        let mut axes: [Vec<f64>; 2] = [vec![], vec![]];
        for polygon in &polygons {
            let n = polygon.points.len();
            for (i, &(x, y)) in polygon.points.iter().enumerate() {
                let prev = polygon.points[(i + n - 1) % n];
                let next = polygon.points[(i + 1) % n];
                let corner = (prev.0 == x || prev.1 == y) && (next.0 == x || next.1 == y);
                if orthogonal || n <= 16 || corner {
                    axes[0].push(x);
                    axes[1].push(y);
                }
            }
            // Ring bounds keep even very small islands and holes represented.
            for axis in 0..2 {
                let values = unique(
                    polygon
                        .points
                        .iter()
                        .map(|p| if axis == 0 { p.0 } else { p.1 }),
                );
                axes[axis].extend([values[0], *values.last().unwrap()]);
            }
        }
        if !orthogonal {
            let longest = (bounds[2] - bounds[0]).max(bounds[3] - bounds[1]);
            for axis in 0..2 {
                let span = bounds[axis + 2] - bounds[axis];
                let count = ((span / longest * resolution as f64).ceil() as usize).max(1);
                axes[axis]
                    .extend((0..=count).map(|i| bounds[axis] + span * i as f64 / count as f64));
            }
        }
        let [x, y] = axes.map(|v| unique(v.into_iter()));
        let width = x.len() - 1;
        let height = y.len() - 1;
        let count = width
            .checked_mul(height)
            .filter(|n| *n <= MAX_PIXELS)
            .ok_or("FAST geometry search exceeds 4000000 cells")?;
        let edges: Vec<_> = polygons
            .iter()
            .flat_map(|p| {
                p.points
                    .iter()
                    .copied()
                    .zip(p.points.iter().copied().cycle().skip(1))
                    .take(p.points.len())
            })
            .filter(|(a, b)| a.1 != b.1)
            .collect();
        let mut foreground = vec![false; count];
        for row in 0..height {
            let yy = (y[row] + y[row + 1]) / 2.;
            let mut crossings: Vec<_> = edges
                .iter()
                .filter_map(|&(a, b)| {
                    (yy >= a.1.min(b.1) && yy < a.1.max(b.1))
                        .then(|| a.0 + (yy - a.1) / (b.1 - a.1) * (b.0 - a.0))
                })
                .collect();
            crossings.sort_by(f64::total_cmp);
            let mut edge = 0;
            for col in 0..width {
                let xx = (x[col] + x[col + 1]) / 2.;
                while edge < crossings.len() && crossings[edge] <= xx {
                    edge += 1;
                }
                foreground[row * width + col] = edge % 2 == 1;
            }
        }
        let mut blocked = vec![false; count];
        // Keep one interior witness per true hole unavailable to all rectangles.
        // The final vector audit still checks the full hole correspondence.
        for hole in polygons.iter().filter(|p| !p.island) {
            let point = original
                .engine
                .polygon(&hole.points, &[])?
                .difference(original)?
                .interior_point()?;
            let col = x
                .partition_point(|v| *v <= point.0)
                .saturating_sub(1)
                .min(width - 1);
            let row = y
                .partition_point(|v| *v <= point.1)
                .saturating_sub(1)
                .min(height - 1);
            blocked[row * width + col] = true;
        }
        Ok(Self {
            x,
            y,
            foreground,
            blocked,
        })
    }
    fn width(&self) -> usize {
        self.x.len() - 1
    }
    fn height(&self) -> usize {
        self.y.len() - 1
    }
    fn rectangle(&self, w: Window) -> Rect {
        [self.x[w[0]], self.y[w[1]], self.x[w[2]], self.y[w[3]]]
    }

    /// Histogram algorithm with physical widths/heights, also on nonuniform axes.
    fn seed(&self) -> Option<Window> {
        let width = self.width();
        let mut starts = vec![0; width];
        let (mut best, mut best_area) = (None, 0.);
        for row in 0..self.height() {
            for (col, start) in starts.iter_mut().enumerate() {
                if !self.foreground[row * width + col] || self.blocked[row * width + col] {
                    *start = row + 1;
                }
            }
            let mut stack: Vec<(usize, usize)> = vec![];
            for col in 0..=width {
                let top = if col == width { row + 1 } else { starts[col] };
                let mut left = col;
                while let Some(&(previous_left, previous_top)) = stack.last() {
                    if previous_top >= top {
                        break;
                    }
                    stack.pop();
                    let candidate = [previous_left, previous_top, col, row + 1];
                    let value = area(self.rectangle(candidate));
                    if value > best_area {
                        best = Some(candidate);
                        best_area = value;
                    }
                    left = previous_left;
                }
                if stack
                    .last()
                    .is_none_or(|&(_, previous_top)| previous_top != top)
                {
                    stack.push((left, top));
                }
            }
        }
        best
    }
    fn cover(&mut self, w: Window) -> (f64, f64) {
        let width = self.width();
        let (mut foreground, mut background) = (0., 0.);
        for row in w[1]..w[3] {
            for col in w[0]..w[2] {
                let index = row * width + col;
                let a = (self.x[col + 1] - self.x[col]) * (self.y[row + 1] - self.y[row]);
                if self.foreground[index] {
                    foreground += a;
                } else {
                    background += a;
                }
                self.blocked[index] = true;
            }
        }
        (foreground, background)
    }
}

struct Integral {
    stride: usize,
    benefit: Vec<f64>,
    blocked: Vec<u32>,
}
impl Integral {
    fn new(mask: &Mask, tau: f64) -> Self {
        let stride = mask.x.len();
        let mut benefit = vec![0.; stride * mask.y.len()];
        let mut blocked = vec![0; benefit.len()];
        for row in 0..mask.height() {
            let (mut row_benefit, mut row_blocked) = (0., 0);
            for col in 0..mask.width() {
                let source = row * mask.width() + col;
                let a = (mask.x[col + 1] - mask.x[col]) * (mask.y[row + 1] - mask.y[row]);
                row_benefit += a * (f64::from(mask.foreground[source]) - tau);
                row_blocked += u32::from(mask.blocked[source]);
                let target = (row + 1) * stride + col + 1;
                benefit[target] = benefit[target - stride] + row_benefit;
                blocked[target] = blocked[target - stride] + row_blocked;
            }
        }
        Self {
            stride,
            benefit,
            blocked,
        }
    }
    fn score(&self, w: Window) -> Option<f64> {
        if w[0] >= w[2] || w[1] >= w[3] {
            return None;
        }
        let [a, b, c, d] = [
            w[1] * self.stride + w[0],
            w[1] * self.stride + w[2],
            w[3] * self.stride + w[0],
            w[3] * self.stride + w[2],
        ];
        if self.blocked[d] + self.blocked[a] != self.blocked[b] + self.blocked[c] {
            return None;
        }
        Some(self.benefit[d] + self.benefit[a] - self.benefit[b] - self.benefit[c])
    }
    fn optimize(&self, mask: &Mask, mut best: Window) -> Window {
        let mut score = self.score(best).unwrap();
        for _ in 0..16 {
            let before = best;
            for edge in 0..4 {
                let limit = if edge % 2 == 0 {
                    mask.width()
                } else {
                    mask.height()
                };
                for coordinate in 0..=limit {
                    let mut candidate = best;
                    candidate[edge] = coordinate;
                    if let Some(value) = self.score(candidate)
                        && value > score + 1e-12 * score.abs().max(1.)
                    {
                        best = candidate;
                        score = value;
                    }
                }
            }
            if before == best {
                break;
            }
        }
        best
    }
}

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
