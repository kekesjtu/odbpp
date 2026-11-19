//! Row and column series-parallel conductivity using band prefix sums.

use crate::sampling::{Window, bulk_properties};

pub(crate) fn integrate(
    pixels: &[u8],
    stride: usize,
    windows: &[Window],
    materials: [f64; 6],
    minimum: usize,
) -> Vec<Option<[f64; 5]>> {
    let [k1, k2, ..] = materials;
    let mut out = vec![None; windows.len()];
    let mut start = 0;
    while start < windows.len() {
        let (_, _, bottom, top) = windows[start];
        let mut end = start + 1;
        while end < windows.len() && windows[end].2 == bottom && windows[end].3 == top {
            end += 1;
        }
        let rows = top - bottom;
        if rows < minimum {
            start = end;
            continue;
        }
        let left = windows[start..end].iter().map(|w| w.0).min().unwrap();
        let right = windows[start..end].iter().map(|w| w.1).max().unwrap();
        let width = right - left;
        let mut prefix = vec![0u32; rows * (width + 1)];
        let mut columns = vec![0u32; width];
        let mut invalid = vec![false; width];
        for row in 0..rows {
            for col in 0..width {
                let pixel = pixels[(bottom + row) * stride + left + col];
                let n = u32::from(pixel == 0);
                prefix[row * (width + 1) + col + 1] = prefix[row * (width + 1) + col] + n;
                columns[col] += n;
                invalid[col] |= pixel != 0 && pixel != 255;
            }
        }
        let series = |fraction: f64| 1. / (fraction / k1 + (1. - fraction) / k2);
        for i in start..end {
            let (a, b) = (windows[i].0 - left, windows[i].1 - left);
            let cols = b - a;
            if cols < minimum || invalid[a..b].iter().any(|v| *v) {
                continue;
            }
            let (mut kx, mut ky, mut copper) = (0., 0., 0u64);
            for row in 0..rows {
                let n = prefix[row * (width + 1) + b] - prefix[row * (width + 1) + a];
                copper += u64::from(n);
                kx += series(n as f64 / cols as f64);
            }
            for &n in &columns[a..b] {
                ky += series(n as f64 / rows as f64);
            }
            let fraction = copper as f64 / (rows * cols) as f64;
            let [kz, density, cp] = bulk_properties(fraction, materials);
            out[i] = Some([kx / rows as f64, ky / cols as f64, kz, density, cp]);
        }
        start = end;
    }
    out
}
