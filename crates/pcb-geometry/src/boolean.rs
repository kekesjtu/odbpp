use crate::{Point, Ring, curves::signed_area};
use clipper2_rust::*;

pub const SCALE: f64 = 1_000_000.;

fn path(points: &[Point], scale: f64) -> Result<Path64, String> {
    if !scale.is_finite() || scale <= 0. {
        return Err("invalid coordinate scale".into());
    }
    let mut out = Vec::with_capacity(points.len());
    for &(x, y) in points {
        let (x, y) = (x * scale, y * scale);
        // Keep far inside Clipper's 62-bit range, including Minkowski additions.
        if !x.is_finite() || !y.is_finite() || x.abs().max(y.abs()) > (1u64 << 59) as f64 {
            return Err("non-finite or out-of-range geometry coordinate".into());
        }
        let p = Point64::new(x.round_ties_even() as i64, y.round_ties_even() as i64);
        if out.last() != Some(&p) {
            out.push(p);
        }
    }
    while out.len() > 1 && out.first() == out.last() {
        out.pop();
    }
    Ok(out)
}

fn paths(rings: &[Ring], scale: f64) -> Result<Paths64, String> {
    rings
        .iter()
        .map(|(points, hole)| {
            let mut p = path(points, scale)?;
            if (area(&p) < 0.) != !hole {
                p.reverse();
            }
            Ok(p)
        })
        .collect()
}

fn resolve(
    subject: &Paths64,
    clip: &Paths64,
    op: ClipType,
    scale: f64,
) -> Result<Vec<Ring>, String> {
    let mut engine = Clipper64::new();
    engine.add_subject(subject);
    engine.add_clip(clip);
    let mut tree = PolyTree64::new();
    if !engine.execute_tree(op, FillRule::NonZero, &mut tree, &mut vec![]) {
        return Err("integer geometry operation failed".into());
    }
    let mut rings = vec![];
    for i in 1..tree.nodes.len() {
        let hole = tree.is_hole(i);
        let mut p: Vec<Point> = tree.nodes[i]
            .polygon()
            .iter()
            .map(|p| (p.x as f64 / scale, p.y as f64 / scale))
            .collect();
        if p.len() < 3 || signed_area(&p).abs() <= 1e-18 {
            continue;
        }
        if (signed_area(&p) < 0.) != !hole {
            p.reverse();
        }
        rings.push((p, hole));
    }
    Ok(rings)
}

pub fn operate(
    operation: &str,
    subject: &[Ring],
    clip: &[Ring],
    scale: f64,
) -> Result<Vec<Ring>, String> {
    let op = match operation {
        "union" => ClipType::Union,
        "difference" => ClipType::Difference,
        "intersection" => ClipType::Intersection,
        _ => return Err(format!("unknown geometry operation {operation}")),
    };
    resolve(&paths(subject, scale)?, &paths(clip, scale)?, op, scale)
}

pub fn snap(rings: &[Ring], scale: f64) -> Result<(Vec<Ring>, usize), String> {
    let mut collapsed = 0;
    let mut kept = vec![];
    for ring in rings {
        let p = path(&ring.0, scale)?;
        if p.len() < 3 || resolve(&vec![p], &vec![], ClipType::Union, scale)?.is_empty() {
            collapsed += 1;
        } else {
            kept.push(ring.clone());
        }
    }
    Ok((operate("union", &kept, &[], scale)?, collapsed))
}

pub fn stroke(lines: &[Vec<Point>], radius: f64, sagitta: f64) -> Result<Vec<Ring>, String> {
    if !radius.is_finite() || !sagitta.is_finite() || sagitta <= 0. {
        return Err("invalid stroke radius or sagitta".into());
    }
    if radius <= 0. {
        return Ok(vec![]);
    }
    let mut offsetter = ClipperOffset::new_default();
    offsetter.set_arc_tolerance((sagitta * SCALE).max(1.));
    for points in lines {
        let closed =
            points.len() >= 4 && path(&[points[0], points[points.len() - 1]], SCALE)?.len() == 1;
        let p = path(points, SCALE)?;
        if p.len() < if closed { 3 } else { 2 } {
            continue;
        }
        offsetter.add_paths(
            &vec![p],
            JoinType::Round,
            if closed {
                EndType::Joined
            } else {
                EndType::Round
            },
        );
    }
    let mut result = vec![];
    offsetter.execute((radius * SCALE).round_ties_even(), &mut result);
    resolve(&result, &vec![], ClipType::Union, SCALE)
}

pub fn offset(rings: &[Ring], delta: f64, sagitta: f64) -> Result<Vec<Ring>, String> {
    if !delta.is_finite() || !sagitta.is_finite() || sagitta <= 0. {
        return Err("invalid offset or sagitta".into());
    }
    if delta == 0. || rings.is_empty() {
        return Ok(vec![]);
    }
    let mut offsetter = ClipperOffset::new_default();
    offsetter.set_arc_tolerance((sagitta * SCALE).max(1.));
    offsetter.add_paths(&paths(rings, SCALE)?, JoinType::Round, EndType::Polygon);
    let mut result = vec![];
    offsetter.execute((delta * SCALE).round_ties_even(), &mut result);
    resolve(&result, &vec![], ClipType::Union, SCALE)
}

pub fn sweep(patterns: &[Vec<Point>], lines: &[Vec<Point>]) -> Result<Vec<Ring>, String> {
    let mut result = vec![];
    for pattern in patterns {
        let mut pattern = path(pattern, SCALE)?;
        if pattern.len() < 3 {
            continue;
        }
        if area(&pattern) < 0. {
            pattern.reverse();
        }
        for points in lines {
            let mut p = path(points, SCALE)?;
            if p.len() < 2 {
                continue;
            }
            if points.len() >= 4 && path(&[points[0], points[points.len() - 1]], SCALE)?.len() == 1
            {
                p.push(p[0]);
            }
            result.extend(minkowski_sum(&pattern, &p, false));
        }
    }
    resolve(&result, &vec![], ClipType::Union, SCALE)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn square(a: f64, b: f64) -> Vec<Point> {
        vec![(a, a), (b, a), (b, b), (a, b)]
    }
    #[test]
    fn nested_holes_have_explicit_winding() {
        let result = operate(
            "union",
            &[
                (square(0., 10.), false),
                (square(2., 8.), true),
                (square(4., 6.), false),
            ],
            &[],
            SCALE,
        )
        .unwrap();
        assert_eq!(result.len(), 3);
        let area: f64 = result
            .iter()
            .map(|(p, h)| signed_area(p).abs() * if *h { -1. } else { 1. })
            .sum();
        assert_eq!(area, 68.);
        for (p, h) in result {
            assert_eq!(signed_area(&p) > 0., h);
        }
    }
    #[test]
    fn quantization_uses_even_ties() {
        assert_eq!(
            path(&[(0.5, 1.5), (2.5, 3.5)], 1.).unwrap(),
            vec![Point64::new(0, 2), Point64::new(2, 4)]
        );
    }
}
