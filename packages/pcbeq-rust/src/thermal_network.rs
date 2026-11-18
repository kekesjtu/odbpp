//! Pixel-centred finite-volume network for in-plane thermal conductivity.

use crate::Result;

pub struct PixelWindow<'a> {
    pub pixels: &'a [u8],
    pub stride: usize,
    pub cols: usize,
    pub rows: usize,
}

impl PixelWindow<'_> {
    pub fn row(&self, row: usize) -> &[u8] {
        &self.pixels[row * self.stride..row * self.stride + self.cols]
    }
}

#[derive(Clone, Copy, Default)]
struct Node {
    east: f64,
    south: f64,
    diagonal: f64,
    inverse_pivot: f64,
}

#[derive(Default)]
pub struct Network {
    nodes: Vec<Node>,
    work: Vec<f64>,
}

impl Network {
    pub fn solve(
        &mut self,
        window: &PixelWindow<'_>,
        pitch: [f64; 2],
        conductivities: [f64; 2],
        along_y: bool,
    ) -> Result<f64> {
        // Orient both solves with the prescribed temperature difference along columns.
        let (width, height, along, transverse) = if along_y {
            (window.rows, window.cols, pitch[1], pitch[0])
        } else {
            (window.cols, window.rows, pitch[0], pitch[1])
        };
        let scale = conductivities[0].max(conductivities[1]);
        let normalized = conductivities.map(|k| k / scale);
        let transverse_weight = (along / transverse).powi(2);
        if !transverse_weight.is_finite() || transverse_weight <= 0. || normalized.contains(&0.) {
            return Err("thermal network conductance is outside the numeric range".into());
        }
        let conductivity = |col, row| {
            let index = if along_y {
                col * window.stride + row
            } else {
                row * window.stride + col
            };
            normalized[usize::from(window.pixels[index] != 0)]
        };
        let count = width * height;
        self.nodes
            .try_reserve(count.saturating_sub(self.nodes.len()))
            .map_err(|e| format!("thermal network nodes: {e}"))?;
        self.nodes.resize(count, Node::default());
        self.work
            .try_reserve((5 * count).saturating_sub(self.work.len()))
            .map_err(|e| format!("thermal network workspace: {e}"))?;
        self.work.resize(5 * count, 0.);
        let (temperature, rest) = self.work.split_at_mut(count);
        let (residual, rest) = rest.split_at_mut(count);
        let (preconditioned, rest) = rest.split_at_mut(count);
        let (direction, product) = rest.split_at_mut(count);
        let nodes = &mut self.nodes;
        let step = 1. / width as f64;
        for row in 0..height {
            for col in 0..width {
                let i = row * width + col;
                let k = conductivity(col, row);
                let east = if col + 1 < width {
                    harmonic(k, conductivity(col + 1, row))
                } else {
                    0.
                };
                let south = if row + 1 < height {
                    transverse_weight * harmonic(k, conductivity(col, row + 1))
                } else {
                    0.
                };
                let (west, west_pivot) = if col > 0 {
                    (nodes[i - 1].east, nodes[i - 1].inverse_pivot)
                } else {
                    (0., 0.)
                };
                let (north, north_pivot) = if row > 0 {
                    (nodes[i - width].south, nodes[i - width].inverse_pivot)
                } else {
                    (0., 0.)
                };
                // Half a pixel from an end face to its first node, with adiabatic sides.
                let left = if col == 0 { k } else { 0. };
                let right = if col + 1 == width { k } else { 0. };
                let diagonal = west + east + north + south + 2. * (left + right);
                let pivot = diagonal - west * (west * west_pivot) - north * (north * north_pivot);
                if !pivot.is_finite() || pivot <= 0. {
                    return Err("thermal network preconditioner has an invalid pivot".into());
                }
                nodes[i] = Node {
                    east,
                    south,
                    diagonal,
                    inverse_pivot: pivot.recip(),
                };
                temperature[i] = (col as f64 + 0.5) * step;
                residual[i] = (east + right - west - left) * step;
            }
        }

        let tolerance_squared = dot(residual, residual) * 1e-20;
        if dot(residual, residual) > 0. {
            precondition(nodes, width, residual, preconditioned);
            direction.copy_from_slice(preconditioned);
            let mut inner = dot(residual, preconditioned);
            let max_iterations = count.min(20_000);
            let mut converged = false;
            for _ in 0..max_iterations {
                multiply(nodes, width, direction, product);
                let alpha = inner / dot(direction, product);
                if !alpha.is_finite() || alpha <= 0. {
                    return Err("thermal network iteration broke down".into());
                }
                let mut residual_squared = 0.;
                for i in 0..count {
                    temperature[i] += alpha * direction[i];
                    residual[i] -= alpha * product[i];
                    residual_squared += residual[i] * residual[i];
                }
                if residual_squared <= tolerance_squared {
                    converged = true;
                    break;
                }
                precondition(nodes, width, residual, preconditioned);
                let next_inner = dot(residual, preconditioned);
                let beta = next_inner / inner;
                for i in 0..count {
                    direction[i] = preconditioned[i] + beta * direction[i];
                }
                inner = next_inner;
            }
            if !converged {
                return Err(format!(
                    "thermal network did not converge in {max_iterations} iterations"
                ));
            }
        }

        // With a unit face temperature difference, the minimized energy equals the end-face flow.
        let mut energy = 0.;
        for row in 0..height {
            for col in 0..width {
                let i = row * width + col;
                let t = temperature[i];
                if col + 1 < width {
                    energy += nodes[i].east * (t - temperature[i + 1]).powi(2);
                } else {
                    energy += 2. * conductivity(col, row) * (1. - t).powi(2);
                }
                if row + 1 < height {
                    energy += nodes[i].south * (t - temperature[i + width]).powi(2);
                }
                if col == 0 {
                    energy += 2. * conductivity(col, row) * t.powi(2);
                }
            }
        }
        Ok((energy * (width as f64 / height as f64)) * scale)
    }
}

fn harmonic(a: f64, b: f64) -> f64 {
    let low = a.min(b);
    low / (0.5 + 0.5 * (low / a.max(b)))
}

fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(a, b)| a * b).sum()
}

fn multiply(nodes: &[Node], width: usize, input: &[f64], output: &mut [f64]) {
    for (i, node) in nodes.iter().enumerate() {
        let mut value = node.diagonal * input[i];
        if i > 0 {
            value -= nodes[i - 1].east * input[i - 1];
        }
        if i + 1 < nodes.len() {
            value -= node.east * input[i + 1];
        }
        if i >= width {
            value -= nodes[i - width].south * input[i - width];
        }
        if i + width < nodes.len() {
            value -= node.south * input[i + width];
        }
        output[i] = value;
    }
}

// Incomplete LDL^T using the two earlier neighbours of each row-major node.
fn precondition(nodes: &[Node], width: usize, residual: &[f64], output: &mut [f64]) {
    for (i, node) in nodes.iter().enumerate() {
        let mut value = residual[i];
        if i > 0 {
            value += nodes[i - 1].east * output[i - 1];
        }
        if i >= width {
            value += nodes[i - width].south * output[i - width];
        }
        output[i] = value * node.inverse_pivot;
    }
    for (i, node) in nodes.iter().enumerate().rev() {
        let mut value = 0.;
        if i + 1 < nodes.len() {
            value += node.east * output[i + 1];
        }
        if i + width < nodes.len() {
            value += node.south * output[i + width];
        }
        output[i] += value * node.inverse_pivot;
    }
}
