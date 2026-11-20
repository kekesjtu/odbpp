//! Row and column series-parallel conductivity for each pixel window.

use crate::sampling::{Window, bulk_properties};

const COMPARISON_WIDTH: usize = 128;
const COPPER: [u8; COMPARISON_WIDTH] = [0; COMPARISON_WIDTH];
const BASE: [u8; COMPARISON_WIDTH] = [255; COMPARISON_WIDTH];

pub(crate) fn integrate(
    pixels: &[u8],
    stride: usize,
    windows: &[Window],
    materials: [f64; 6],
) -> Vec<Option<[f64; 5]>> {
    let [k1, k2, ..] = materials;
    let series = |fraction: f64| 1. / (fraction / k1 + (1. - fraction) / k2);
    let mut columns = Vec::new();
    windows
        .iter()
        .map(|&(left, right, bottom, top)| {
            let (cols, rows) = (right - left, top - bottom);
            let row_pixels = |row| &pixels[row * stride + left..row * stride + right];
            let (fill, pure_fraction) = match row_pixels(bottom)[0] {
                0 => (&COPPER, 1.),
                255 => (&BASE, 0.),
                _ => return None,
            };
            let uniform = (bottom..top).all(|row| {
                row_pixels(row)
                    .chunks(COMPARISON_WIDTH)
                    .all(|chunk| chunk == &fill[..chunk.len()])
            });
            if uniform {
                let k = series(pure_fraction);
                // Preserve the row and column accumulation, including floating-point rounding.
                let kx = (0..rows).fold(0., |sum, _| sum + k) / rows as f64;
                let ky = (0..cols).fold(0., |sum, _| sum + k) / cols as f64;
                let [kz, density, cp] = bulk_properties(pure_fraction, materials);
                return Some([kx, ky, kz, density, cp]);
            }

            columns.clear();
            columns.resize(cols, 0usize);
            let (mut kx, mut copper) = (0., 0);
            for row in bottom..top {
                let mut row_copper = 0;
                for (&pixel, column) in row_pixels(row).iter().zip(&mut columns) {
                    match pixel {
                        0 => {
                            row_copper += 1;
                            *column += 1;
                        }
                        255 => (),
                        _ => return None,
                    }
                }
                copper += row_copper;
                kx += series(row_copper as f64 / cols as f64);
            }
            let ky = columns
                .iter()
                .fold(0., |sum, &n| sum + series(n as f64 / rows as f64));
            let [kz, density, cp] =
                bulk_properties(copper as f64 / (rows * cols) as f64, materials);
            Some([kx / rows as f64, ky / cols as f64, kz, density, cp])
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const MATERIALS: [f64; 6] = [385., 0.3, 8960., 1850., 385., 1100.];

    #[test]
    fn uniform_window_preserves_directional_rounding_and_rejects_invalid_pixels() {
        let stride = 132;
        let window = [(1, 131, 0, 3)];
        let mut pixels = vec![255; stride * 3];
        pixels[0] = 42;
        let k = 1. / (1. / MATERIALS[1]);
        let ky = (0..130).fold(0., |sum, _| sum + k) / 130.;
        assert_eq!(
            integrate(&pixels, stride, &window, MATERIALS),
            vec![Some([(k + k + k) / 3., ky, 0.3, 1850., 1100.])]
        );
        pixels[2 * stride + 130] = 42;
        assert_eq!(integrate(&pixels, stride, &window, MATERIALS), vec![None]);
    }

    #[test]
    fn mixed_window_reduces_rows_and_columns_separately() {
        let pixels = [0, 255, 0, 0, 255, 255];
        let series = |f: f64| 1. / (f / MATERIALS[0] + (1. - f) / MATERIALS[1]);
        let [kz, density, cp] = bulk_properties(0.5, MATERIALS);
        assert_eq!(
            integrate(&pixels, 2, &[(0, 2, 0, 3)], MATERIALS),
            vec![Some([
                (series(0.5) + series(1.) + series(0.)) / 3.,
                (series(2. / 3.) + series(1. / 3.)) / 2.,
                kz,
                density,
                cp,
            ])]
        );
    }
}
