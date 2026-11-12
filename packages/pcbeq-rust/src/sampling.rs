//! Band prefix sums preserve row/column series-parallel conductivity.
type Window = (usize, usize, usize, usize);

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn series_parallel_anisotropy_and_mass_weighting() {
        let materials = [400., 1., 8000., 2000., 400., 1000.];
        let horizontal: Vec<_> = [vec![0; 8], vec![255; 8]].concat();
        let values = integrate_pixels(&horizontal, 4, 4, vec![(0, 4, 0, 4)], materials, 1).unwrap()
            [0]
        .unwrap();
        let expected = [0.5, 200.5, 2. / (1. / 400. + 1.), 200.5, 5000., 520.];
        for (actual, expected) in values.into_iter().zip(expected) {
            assert!((actual - expected).abs() < 1e-10);
        }
        let vertical = [0, 0, 255, 255].repeat(4);
        let rotated = integrate_pixels(&vertical, 4, 4, vec![(0, 4, 0, 4)], materials, 1).unwrap()
            [0]
        .unwrap();
        assert!((rotated[1] - values[2]).abs() < 1e-10);
        assert!((rotated[2] - values[1]).abs() < 1e-10);
    }

    #[test]
    fn band_prefix_matches_direct_pixel_reference() {
        let (width, height) = (31, 23);
        let pixels: Vec<_> = (0..width * height)
            .map(|i| {
                if (i * 37 + i * i * 13) % 101 < 37 {
                    0
                } else {
                    255
                }
            })
            .collect();
        let windows = vec![(0, 9, 0, 7), (9, 31, 0, 7), (1, 28, 7, 23)];
        let results = integrate_pixels(
            &pixels,
            width,
            height,
            windows.clone(),
            [385., 0.3, 8960., 1850., 385., 1100.],
            1,
        )
        .unwrap();
        for ((a, b, c, d), result) in windows.into_iter().zip(results) {
            let black = |x: usize, y: usize| f64::from(pixels[y * width + x] == 0);
            let average = |p: f64| 1. / (p / 385. + (1. - p) / 0.3);
            let kx = (c..d)
                .map(|y| average((a..b).map(|x| black(x, y)).sum::<f64>() / (b - a) as f64))
                .sum::<f64>()
                / (d - c) as f64;
            let ky = (a..b)
                .map(|x| average((c..d).map(|y| black(x, y)).sum::<f64>() / (d - c) as f64))
                .sum::<f64>()
                / (b - a) as f64;
            let phi = (c..d)
                .flat_map(|y| (a..b).map(move |x| black(x, y)))
                .sum::<f64>()
                / ((b - a) * (d - c)) as f64;
            let density = phi * 8960. + (1. - phi) * 1850.;
            let cp = (phi * 8960. * 385. + (1. - phi) * 1850. * 1100.) / density;
            for (actual, expected) in result.unwrap().into_iter().zip([
                phi,
                kx,
                ky,
                phi * 385. + (1. - phi) * 0.3,
                density,
                cp,
            ]) {
                assert!((actual - expected).abs() < 1e-9);
            }
        }
    }

    #[test]
    fn incomplete_samples_and_invalid_windows_are_rejected() {
        for (pixels, window, minimum) in [
            (vec![0, 255, 160, 255], (0, 2, 0, 2), 1),
            (vec![0; 4], (0, 0, 0, 2), 1),
            (vec![0; 4], (0, 2, 0, 2), 3),
        ] {
            assert_eq!(
                integrate_pixels(&pixels, 2, 2, vec![window], [1.; 6], minimum).unwrap(),
                vec![None]
            );
        }
        assert!(integrate_pixels(&[], 1, 1, vec![], [1.; 6], 1).is_err());
        assert!(integrate_pixels(&[0], 1, 1, vec![(0, 2, 0, 1)], [1.; 6], 1).is_err());
    }
}
