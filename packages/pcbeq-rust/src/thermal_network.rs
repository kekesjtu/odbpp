//! Pixel-centred finite-volume network for in-plane thermal conductivity.

use crate::Result;
use faer::{
    Conj, MatMut, Par, Side,
    dyn_stack::{MemBuffer, MemStack, StackReq},
    sparse::{
        SparseColMatRef, SymbolicSparseColMat,
        linalg::cholesky::{SymbolicCholesky, SymmetricOrdering, factorize_symbolic_cholesky},
    },
};

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

#[derive(Default)]
pub struct Network {
    system: Option<SparseSystem>,
}

struct SparseSystem {
    size: [usize; 2],
    pattern: SymbolicSparseColMat<usize>,
    symbolic: SymbolicCholesky<usize>,
    values: Vec<f64>,
    factor: Vec<f64>,
    temperature: Vec<f64>,
    scratch: MemBuffer,
}

impl Network {
    pub fn solve(
        &mut self,
        window: &PixelWindow<'_>,
        pitch: [f64; 2],
        conductivities: [f64; 2],
        along_y: bool,
    ) -> Result<f64> {
        let size = [window.cols, window.rows];
        let axis = usize::from(along_y);
        let transverse = 1 - axis;
        let scale = conductivities[0].max(conductivities[1]);
        let normalized = conductivities.map(|k| k / scale);
        let mut weights = [1.; 2];
        weights[transverse] = (pitch[axis] / pitch[transverse]).powi(2);
        if !weights[transverse].is_finite() || weights[transverse] <= 0. || normalized.contains(&0.)
        {
            return Err("thermal network conductance is outside the numeric range".into());
        }

        // Both directions and successive windows of the same shape share the CSC pattern.
        if self
            .system
            .as_ref()
            .is_none_or(|system| system.size != size)
        {
            self.system = Some(SparseSystem::new(size)?);
        }
        let system = self.system.as_mut().unwrap();
        let energy = system.solve(window, normalized, weights, axis)?;
        Ok((energy * (size[axis] as f64 / size[transverse] as f64)) * scale)
    }
}

impl SparseSystem {
    fn new([cols, rows]: [usize; 2]) -> Result<Self> {
        let count = cols * rows;
        let nonzeros = 3 * count - cols - rows;
        let mut col_ptr = Vec::new();
        let mut row_idx = Vec::new();
        col_ptr
            .try_reserve_exact(count + 1)
            .map_err(workspace_error)?;
        row_idx
            .try_reserve_exact(nonzeros)
            .map_err(workspace_error)?;
        for y in 0..rows {
            for x in 0..cols {
                let node = y * cols + x;
                col_ptr.push(row_idx.len());
                row_idx.push(node);
                if x + 1 < cols {
                    row_idx.push(node + 1);
                }
                if y + 1 < rows {
                    row_idx.push(node + cols);
                }
            }
        }
        col_ptr.push(row_idx.len());
        let pattern = SymbolicSparseColMat::new_checked(count, count, col_ptr, None, row_idx);
        let symbolic = factorize_symbolic_cholesky(
            pattern.as_ref(),
            Side::Lower,
            SymmetricOrdering::Amd,
            Default::default(),
        )
        .map_err(|e| format!("sparse Cholesky symbolic analysis: {e:?}"))?;
        let scratch = MemBuffer::try_new(StackReq::any_of(&[
            symbolic.factorize_numeric_llt_scratch::<f64>(Par::Seq, Default::default()),
            symbolic.solve_in_place_scratch::<f64>(1, Par::Seq),
        ]))
        .map_err(workspace_error)?;
        let mut values = Vec::new();
        let mut factor = Vec::new();
        let mut temperature = Vec::new();
        values
            .try_reserve_exact(nonzeros)
            .map_err(workspace_error)?;
        factor
            .try_reserve_exact(symbolic.len_val())
            .map_err(workspace_error)?;
        temperature
            .try_reserve_exact(count)
            .map_err(workspace_error)?;
        values.resize(nonzeros, 0.);
        factor.resize(symbolic.len_val(), 0.);
        temperature.resize(count, 0.);
        Ok(Self {
            size: [cols, rows],
            pattern,
            symbolic,
            values,
            factor,
            temperature,
            scratch,
        })
    }

    fn solve(
        &mut self,
        window: &PixelWindow<'_>,
        conductivities: [f64; 2],
        weights: [f64; 2],
        axis: usize,
    ) -> Result<f64> {
        let [cols, rows] = self.size;
        let col_ptr = self.pattern.col_ptr();
        let row_idx = self.pattern.row_idx();
        let conductivity =
            |x, y| conductivities[usize::from(window.pixels[y * window.stride + x] != 0)];
        self.values.fill(0.);
        for y in 0..rows {
            for x in 0..cols {
                let node = y * cols + x;
                let column = col_ptr[node];
                let k = conductivity(x, y);
                for entry in column + 1..col_ptr[node + 1] {
                    let neighbor = row_idx[entry];
                    let g = if neighbor - node == cols {
                        weights[1] * harmonic(k, conductivity(x, y + 1))
                    } else {
                        weights[0] * harmonic(k, conductivity(x + 1, y))
                    };
                    self.values[entry] = -g;
                    self.values[column] += g;
                    self.values[col_ptr[neighbor]] += g;
                }
                // Half-pixel branches to the fixed-temperature faces, zero and one.
                let coord = [x, y][axis];
                let low = if coord == 0 { 2. * k } else { 0. };
                let high = if coord + 1 == self.size[axis] {
                    2. * k
                } else {
                    0.
                };
                self.values[column] += low + high;
                self.temperature[node] = high;
            }
        }
        let matrix = SparseColMatRef::new(self.pattern.as_ref(), &self.values);
        let scratch = MemStack::new(&mut self.scratch);
        let llt = self
            .symbolic
            .factorize_numeric_llt(
                &mut self.factor,
                matrix,
                Side::Lower,
                Default::default(),
                Par::Seq,
                scratch,
                Default::default(),
            )
            .map_err(|e| format!("sparse Cholesky numeric factorization: {e:?}"))?;
        llt.solve_in_place_with_conj(
            Conj::No,
            MatMut::from_column_major_slice_mut(&mut self.temperature, cols * rows, 1),
            Par::Seq,
            scratch,
        );

        // With a unit face temperature difference, the minimized energy equals the end-face flow.
        let mut energy = 0.;
        for y in 0..rows {
            for x in 0..cols {
                let node = y * cols + x;
                let t = self.temperature[node];
                for entry in col_ptr[node] + 1..col_ptr[node + 1] {
                    energy -= self.values[entry] * (t - self.temperature[row_idx[entry]]).powi(2);
                }
                let coord = [x, y][axis];
                if coord == 0 {
                    energy += 2. * conductivity(x, y) * t.powi(2);
                }
                if coord + 1 == self.size[axis] {
                    energy += 2. * conductivity(x, y) * (1. - t).powi(2);
                }
            }
        }
        Ok(energy)
    }
}

fn workspace_error(error: impl std::fmt::Debug) -> String {
    format!("sparse Cholesky workspace: {error:?}")
}

fn harmonic(a: f64, b: f64) -> f64 {
    let low = a.min(b);
    low / (0.5 + 0.5 * (low / a.max(b)))
}
