pub const MAX_AXIS_LINES: usize = 1_000_001;
pub const MAX_GRID_CELLS: usize = 5_000_000;

pub fn axis(left: f64, right: f64, maximum: f64) -> Result<Vec<f64>, String> {
    let ratio = (right - left) / maximum;
    if !left.is_finite()
        || !right.is_finite()
        || right <= left
        || !maximum.is_finite()
        || maximum <= 0.
    {
        return Err("axis endpoints and maximum must be finite, increasing and positive".into());
    }
    if !ratio.is_finite() || ratio > (MAX_AXIS_LINES - 1) as f64 {
        return Err(format!(
            "interval [{left}, {right}] mm, length={}, max={maximum}, count={ratio}",
            right - left
        ));
    }
    let n = (ratio.ceil() as usize).max(1);
    let mut out = vec![left];
    out.extend((1..n).map(|i| left + (right - left) * i as f64 / n as f64));
    out.push(right);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uniform_axes_preserve_endpoints_and_short_spans() {
        let x = axis(-2., 0., 0.7).unwrap();
        assert_eq!(x.len(), 4);
        assert_eq!((x[0], x[3]), (-2., 0.));
        assert!(
            x.windows(2)
                .all(|w| ((w[1] - w[0]) - 2. / 3.).abs() < 1e-12)
        );
        assert_eq!(axis(0., 0.1, 1.).unwrap(), vec![0., 0.1]);
    }

    #[test]
    fn non_finite_and_excessive_axes_fail() {
        assert!(axis(0., 10., 1e-20).is_err());
        assert!(axis(0., 10., f64::NAN).is_err());
    }
}
