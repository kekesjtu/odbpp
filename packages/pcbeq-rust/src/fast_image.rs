//! Uniform binary image and summed-area tables used by FAST equations (4)-(5).
pub type Window = [usize; 4];
pub fn area(w: Window) -> usize {
    (w[2] - w[0]) * (w[3] - w[1])
}

pub struct Image {
    pub width: usize,
    pub height: usize,
    pub foreground: Vec<bool>,
}

pub struct Integral {
    stride: usize,
    foreground: Vec<u32>,
    unavailable: Vec<u32>,
}

impl Integral {
    pub fn new(image: &Image, covered: &[bool], disabled: &[bool]) -> Self {
        let stride = image.width + 1;
        let size = stride * (image.height + 1);
        let mut foreground = vec![0; size];
        let mut unavailable = vec![0; size];
        for row in 0..image.height {
            let (mut count, mut blocked) = (0, 0);
            for col in 0..image.width {
                let source = row * image.width + col;
                let excluded = covered[source] || disabled[source];
                count += u32::from(image.foreground[source] && !excluded);
                blocked += u32::from(excluded);
                let target = (row + 1) * stride + col + 1;
                foreground[target] = foreground[target - stride] + count;
                unavailable[target] = unavailable[target - stride] + blocked;
            }
        }
        Self {
            stride,
            foreground,
            unavailable,
        }
    }

    fn sum(&self, table: &[u32], w: Window) -> usize {
        let [a, b, c, d] = [
            w[1] * self.stride + w[0],
            w[1] * self.stride + w[2],
            w[3] * self.stride + w[0],
            w[3] * self.stride + w[2],
        ];
        (table[d] + table[a] - table[b] - table[c]) as usize
    }
    pub fn count(&self, w: Window) -> usize {
        self.sum(&self.foreground, w)
    }
    pub fn available(&self, w: Window) -> bool {
        self.sum(&self.unavailable, w) == 0
    }

    /// tau=19/20: use 20*F to compare benefits exactly, without float ties.
    pub fn benefit(&self, w: Window) -> i64 {
        20 * self.count(w) as i64 - 19 * area(w) as i64
    }

    /// Equation (5): exhaustive best placement at half width and half height,
    /// followed recursively until the chosen window is wholly foreground.
    pub fn seed(&self, width: usize, height: usize) -> Option<Window> {
        let mut parent = [0, 0, width, height];
        if self.count(parent) == 0 {
            return None;
        }
        loop {
            let w = ((parent[2] - parent[0]) / 2).max(1);
            let h = ((parent[3] - parent[1]) / 2).max(1);
            let mut best = [parent[0], parent[1], parent[0] + w, parent[1] + h];
            let mut maximum = self.count(best);
            for y in parent[1]..=parent[3] - h {
                for x in parent[0]..=parent[2] - w {
                    let candidate = [x, y, x + w, y + h];
                    let count = self.count(candidate);
                    // All windows have equal area, so count maximizes F.
                    if count > maximum {
                        maximum = count;
                        best = candidate;
                    }
                }
            }
            if maximum == area(best) {
                return Some(best);
            }
            parent = best;
        }
    }

    /// Move one edge by one pixel, choosing a strictly improving benefit.
    /// Continue until no admissible single-edge move improves equation (4).
    pub fn optimize(&self, width: usize, height: usize, mut best: Window) -> (Window, usize) {
        let mut moves = 0;
        loop {
            let mut next = best;
            let mut score = self.benefit(best);
            for edge in 0..4 {
                for direction in [-1isize, 1] {
                    let coordinate = best[edge] as isize + direction;
                    let limit = if edge % 2 == 0 { width } else { height };
                    if coordinate < 0 || coordinate as usize > limit {
                        continue;
                    }
                    let mut candidate = best;
                    candidate[edge] = coordinate as usize;
                    if candidate[0] >= candidate[2]
                        || candidate[1] >= candidate[3]
                        || !self.available(candidate)
                    {
                        continue;
                    }
                    let value = self.benefit(candidate);
                    if value > score {
                        next = candidate;
                        score = value;
                    }
                }
            }
            if next == best {
                return (best, moves);
            }
            best = next;
            moves += 1;
        }
    }
}
