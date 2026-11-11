//! Physical-area benefit tables and non-overlapping edge optimization.
use super::{Window, mask::Mask};

pub(super) struct Integral {
    stride: usize,
    benefit: Vec<f64>,
    blocked: Vec<u32>,
}
impl Integral {
    pub(super) fn new(mask: &Mask, tau: f64) -> Self {
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
    pub(super) fn optimize(&self, mask: &Mask, mut best: Window) -> Window {
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
