//! Extract the material properties of each pixel window.

use crate::{
    Result,
    thermal_network::{Network, PixelWindow},
};
use rayon::prelude::*;

type Window = (usize, usize, usize, usize);

pub fn integrate_pixels(
    pixels: &[u8],
    [stride, height]: [usize; 2],
    pitch: [f64; 2],
    windows: &[Window],
    materials: [f64; 6],
    minimum: usize,
) -> Result<Vec<Option<[f64; 5]>>> {
    if stride.checked_mul(height) != Some(pixels.len())
        || stride == 0
        || height == 0
        || pixels.len() > crate::raster::MAX_RASTER_PIXELS
    {
        return Err("mask dimensions do not match pixels or exceed resource limit".into());
    }
    if minimum == 0
        || materials
            .iter()
            .chain(&pitch)
            .any(|v| !v.is_finite() || *v <= 0.)
    {
        return Err(
            "materials, pixel pitch and sampling minimum must be finite and positive".into(),
        );
    }
    if windows
        .iter()
        .any(|&(a, b, c, d)| a > b || b > stride || c > d || d > height)
    {
        return Err("sampling window outside raster".into());
    }

    windows
        .par_iter()
        .with_min_len(
            windows
                .len()
                .div_ceil(rayon::current_num_threads() * 4)
                .max(1),
        )
        .enumerate()
        .map_init(
            Network::default,
            |network, (cell, &(left, right, bottom, top))| {
                let (cols, rows) = (right - left, top - bottom);
                if cols < minimum || rows < minimum {
                    return Ok(None);
                }
                let window = PixelWindow {
                    pixels: &pixels[bottom * stride + left..],
                    stride,
                    cols,
                    rows,
                };
                properties(&window, pitch, materials, network)
                    .map_err(|e| format!("cell {cell}: {e}"))
            },
        )
        .collect()
}

fn properties(
    window: &PixelWindow<'_>,
    pitch: [f64; 2],
    [k1, k2, d1, d2, c1, c2]: [f64; 6],
    network: &mut Network,
) -> Result<Option<[f64; 5]>> {
    let first = window.row(0);
    let mut identical_rows = true;
    let mut constant_rows = true;
    let mut copper = 0;
    for y in 0..window.rows {
        let row = window.row(y);
        let mut row_copper = 0;
        for pixel in row {
            match pixel {
                0 => row_copper += 1,
                255 => (),
                _ => return Ok(None),
            }
        }
        copper += row_copper;
        identical_rows = identical_rows && row == first;
        constant_rows = constant_rows && (row_copper == 0 || row_copper == window.cols);
    }
    let count = window.cols * window.rows;
    let fraction = copper as f64 / count as f64;
    let kz = fraction * k1 + (1. - fraction) * k2;
    let (kx, ky) = if copper == 0 || copper == count || k1 == k2 {
        (kz, kz)
    } else if constant_rows || identical_rows {
        // A stratified network has exact one-dimensional series and parallel reductions.
        let low = k1.min(k2);
        let across_strata = low / (fraction * (low / k1) + (1. - fraction) * (low / k2));
        if constant_rows {
            (kz, across_strata)
        } else {
            (across_strata, kz)
        }
    } else {
        (
            network
                .solve(window, pitch, [k1, k2], false)
                .map_err(|e| format!("X: {e}"))?,
            network
                .solve(window, pitch, [k1, k2], true)
                .map_err(|e| format!("Y: {e}"))?,
        )
    };
    let density = fraction * d1 + (1. - fraction) * d2;
    let cp = (fraction * d1 * c1 + (1. - fraction) * d2 * c2) / density;
    Ok(Some([kx, ky, kz, density, cp]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(pixels: &[u8], size: [usize; 2], pitch: [f64; 2], materials: [f64; 6]) -> [f64; 5] {
        integrate_pixels(
            pixels,
            size,
            pitch,
            &[(0, size[0], 0, size[1])],
            materials,
            1,
        )
        .unwrap()[0]
            .unwrap()
    }

    #[test]
    fn strata_match_series_parallel_and_mass_weighting() {
        let materials = [400., 1., 8000., 2000., 400., 1000.];
        let horizontal = [vec![0; 8], vec![255; 8]].concat();
        let values = cell(&horizontal, [4, 4], [2., 1.], materials);
        let expected = [200.5, 2. / (1. / 400. + 1.), 200.5, 5000., 520.];
        for (actual, expected) in values.into_iter().zip(expected) {
            assert!((actual - expected).abs() < 1e-10);
        }
        let vertical = [0, 0, 255, 255].repeat(4);
        let rotated = cell(&vertical, [4, 4], [1., 2.], materials);
        assert!((rotated[0] - values[1]).abs() < 1e-10);
        assert!((rotated[1] - values[0]).abs() < 1e-10);
    }

    #[test]
    fn checkerboard_diffuses_in_both_directions_using_the_pixel_aspect_ratio() {
        let values = cell(
            &[0, 255, 255, 0],
            [2, 2],
            [2., 1.],
            [2., 1., 1., 1., 1., 1.],
        );
        // Solving the four node equations with both half-pixel end resistances.
        assert!((values[0] - 60. / 43.).abs() < 1e-10);
        assert!((values[1] - 120. / 89.).abs() < 1e-10);
        assert_eq!(values[2..], [1.5, 1., 1.]);
    }

    #[test]
    fn incomplete_samples_and_invalid_windows_are_rejected() {
        for (pixels, window, minimum) in [
            (vec![0, 255, 160, 255], (0, 2, 0, 2), 1),
            (vec![0; 4], (0, 0, 0, 2), 1),
            (vec![0; 4], (0, 2, 0, 2), 3),
        ] {
            assert_eq!(
                integrate_pixels(&pixels, [2, 2], [1.; 2], &[window], [1.; 6], minimum).unwrap(),
                vec![None]
            );
        }
        assert!(integrate_pixels(&[], [1, 1], [1.; 2], &[], [1.; 6], 1).is_err());
        assert!(integrate_pixels(&[0], [1, 1], [1.; 2], &[(0, 2, 0, 1)], [1.; 6], 1).is_err());
    }
}
