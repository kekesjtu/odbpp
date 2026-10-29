use crate::Point;
use std::f64::consts::{FRAC_PI_2, TAU};

fn count(sweep: f64, radius: f64, sagitta: f64, maximum: usize) -> Result<usize, String> {
    if !sagitta.is_finite() || sagitta <= 0. || maximum == 0 || maximum > 1_000_000 {
        return Err("invalid sagitta or segment resource limit".into());
    }
    if !sweep.is_finite() || !radius.is_finite() {
        return Err("non-finite arc".into());
    }
    let n = if radius <= 0. || sagitta >= radius {
        (sweep.abs() / FRAC_PI_2).ceil()
    } else {
        let half = (1. - sagitta / radius).clamp(-1., 1.).acos();
        if half <= 0. {
            maximum as f64
        } else {
            (sweep.abs() / (2. * half)).ceil()
        }
    };
    Ok((n as usize).max(1).min(maximum))
}

pub fn arc_vertices(
    center: Point,
    radius: f64,
    start: f64,
    sweep: f64,
    sagitta: f64,
    maximum: usize,
) -> Result<Vec<Point>, String> {
    if !center.0.is_finite() || !center.1.is_finite() || !start.is_finite() {
        return Err("non-finite arc coordinate".into());
    }
    let n = count(sweep, radius, sagitta, maximum)?;
    if radius <= 0. {
        return Ok(vec![]);
    }
    Ok((0..=n)
        .map(|i| {
            let angle = start + sweep * i as f64 / n as f64;
            (
                center.0 + radius * angle.cos(),
                center.1 + radius * angle.sin(),
            )
        })
        .collect())
}

pub fn arc_points(
    start: Point,
    end: Point,
    center: Point,
    clockwise: bool,
    sagitta: f64,
    maximum: usize,
) -> Result<Vec<Point>, String> {
    let (dx1, dy1) = (start.0 - center.0, start.1 - center.1);
    let (dx2, dy2) = (end.0 - center.0, end.1 - center.1);
    let (r1, r2) = (dx1.hypot(dy1), dx2.hypot(dy2));
    if r1 <= 0. || r2 <= 0. {
        return Ok(vec![end]);
    }
    let radius = (r1 + r2) * 0.5;
    let (a1, a2) = (dy1.atan2(dx1), dy2.atan2(dx2));
    let mut sweep = if (dx2 - dx1).hypot(dy2 - dy1) <= 1e-12_f64.max(radius * 1e-9) {
        if clockwise { -TAU } else { TAU }
    } else if clockwise {
        -(a1 - a2).rem_euclid(TAU)
    } else {
        (a2 - a1).rem_euclid(TAU)
    };
    if sweep == 0. {
        sweep = if clockwise { -TAU } else { TAU };
    }
    let mut points = arc_vertices(center, radius, a1, sweep, sagitta, maximum)?;
    points.remove(0);
    if let Some(last) = points.last_mut() {
        *last = end;
    }
    Ok(points)
}

pub fn signed_area(points: &[Point]) -> f64 {
    if points.is_empty() {
        return 0.;
    }
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .map(|(&(x, y), &(u, v))| x * v - u * y)
        .sum::<f64>()
        * 0.5
}

pub fn transform(points: &[Point], angle: f64, mirrored: bool) -> Vec<Point> {
    let (s, c) = angle.to_radians().sin_cos();
    points
        .iter()
        .map(|&(x, y)| {
            let (u, v) = (x * c + y * s, -x * s + y * c);
            (u, if mirrored { -v } else { v })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn full_circle_preserves_end_and_winding() {
        let p = arc_points((1., 0.), (1., 0.), (0., 0.), true, 0.001, 4096).unwrap();
        assert_eq!(p.last(), Some(&(1., 0.)));
        assert!(signed_area(&p) < -3.13);
    }
    #[test]
    fn rotate_then_mirror() {
        let p = transform(&[(1., 0.)], 90., true);
        assert!((p[0].1 - 1.).abs() < 1e-12);
    }
}
