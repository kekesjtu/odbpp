//! Band prefix sums preserve row/column series-parallel conductivity.
use pyo3::{exceptions::PyValueError, prelude::*};
type Window = (usize, usize, usize, usize);

#[pyfunction]
pub fn integrate(
    py: Python<'_>,
    pixels: &[u8],
    px: usize,
    py_count: usize,
    windows: Vec<Window>,
    materials: [f64; 6],
    minimum: usize,
) -> PyResult<Vec<Option<[f64; 6]>>> {
    py.detach(|| integrate_pixels(pixels, px, py_count, windows, materials, minimum))
        .map_err(PyValueError::new_err)
}

pub fn integrate_pixels(
    pixels: &[u8],
    px: usize,
    py_count: usize,
    windows: Vec<Window>,
    materials: [f64; 6],
    minimum: usize,
) -> Result<Vec<Option<[f64; 6]>>, String> {
    if px.checked_mul(py_count) != Some(pixels.len())
        || px == 0
        || py_count == 0
        || pixels.len() > crate::raster::MAX_RASTER_PIXELS
    {
        return Err("mask dimensions do not match pixels or exceed resource limit".into());
    }
    if minimum == 0 || materials.iter().any(|v| !v.is_finite() || *v <= 0.) {
        return Err("materials and sampling minimum must be finite and positive".into());
    }
    if windows
        .iter()
        .any(|&(a, b, c, d)| a > b || b > px || c > d || d > py_count)
    {
        return Err("sampling window outside raster".into());
    }
    Ok({
        let [k1, k2, d1, d2, c1, c2] = materials;
        let mut out = vec![None; windows.len()];
        let mut start = 0;
        while start < windows.len() {
            let (_, _, b0, b1) = windows[start];
            let mut end = start + 1;
            while end < windows.len() && windows[end].2 == b0 && windows[end].3 == b1 {
                end += 1;
            }
            let rows = b1 - b0;
            if rows < minimum {
                start = end;
                continue;
            }
            let xlo = windows[start..end].iter().map(|w| w.0).min().unwrap();
            let xhi = windows[start..end].iter().map(|w| w.1).max().unwrap();
            let width = xhi - xlo;
            let mut prefix = vec![0u32; rows * (width + 1)];
            let mut columns = vec![0u32; width];
            let mut outside = vec![false; width];
            for row in 0..rows {
                for col in 0..width {
                    let value = pixels[(b0 + row) * px + xlo + col];
                    let n = u32::from(value == 0);
                    prefix[row * (width + 1) + col + 1] = prefix[row * (width + 1) + col] + n;
                    columns[col] += n;
                    outside[col] |= value != 0 && value != 255;
                }
            }
            for i in start..end {
                let (left, right, _, _) = windows[i];
                let (a, b) = (left - xlo, right - xlo);
                let cols = b - a;
                if cols < minimum || outside[a..b].iter().any(|v| *v) {
                    continue;
                }
                let (mut kx, mut ky, mut total) = (0., 0., 0u64);
                for row in 0..rows {
                    let n = prefix[row * (width + 1) + b] - prefix[row * (width + 1) + a];
                    total += u64::from(n);
                    let phi = n as f64 / cols as f64;
                    kx += 1. / (phi / k1 + (1. - phi) / k2);
                }
                for &n in &columns[a..b] {
                    let phi = n as f64 / rows as f64;
                    ky += 1. / (phi / k1 + (1. - phi) / k2);
                }
                let phi = total as f64 / (rows * cols) as f64;
                let density = phi * d1 + (1. - phi) * d2;
                let cp = (phi * d1 * c1 + (1. - phi) * d2 * c2) / density;
                out[i] = Some([
                    phi,
                    kx / rows as f64,
                    ky / cols as f64,
                    phi * k1 + (1. - phi) * k2,
                    density,
                    cp,
                ]);
            }
            start = end;
        }
        out
    })
}
