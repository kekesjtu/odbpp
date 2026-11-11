//! Feature-aware foreground search and largest-rectangle seeds.
use super::{MAX_PIXELS, Window};
use crate::{
    document,
    geos::{Result, Shape},
    rectangles::{Rect, area, unique},
};

pub(super) struct Mask {
    pub(super) x: Vec<f64>,
    pub(super) y: Vec<f64>,
    pub(super) foreground: Vec<bool>,
    pub(super) blocked: Vec<bool>,
}

impl Mask {
    pub(super) fn new(original: &Shape<'_>, resolution: usize) -> Result<Self> {
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
    pub(super) fn width(&self) -> usize {
        self.x.len() - 1
    }
    pub(super) fn height(&self) -> usize {
        self.y.len() - 1
    }
    pub(super) fn rectangle(&self, w: Window) -> Rect {
        [self.x[w[0]], self.y[w[1]], self.x[w[2]], self.y[w[3]]]
    }

    /// Histogram algorithm with physical widths/heights, also on nonuniform axes.
    pub(super) fn seed(&self) -> Option<Window> {
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
    pub(super) fn cover(&mut self, w: Window) -> (f64, f64) {
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
