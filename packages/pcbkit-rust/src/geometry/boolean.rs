use super::{Point, Ring, curves::signed_area};
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
        "xor" => ClipType::Xor,
        _ => return Err(format!("unknown geometry operation {operation}")),
    };
    resolve(&paths(subject, scale)?, &paths(clip, scale)?, op, scale)
}

pub fn cut_lines(lines: &[Vec<Point>], cutters: &[Ring]) -> Result<Vec<Vec<Point>>, String> {
    let open = lines
        .iter()
        .map(|line| path(line, SCALE))
        .collect::<Result<_, _>>()?;
    let mut engine = Clipper64::new();
    engine.add_open_subject(&open);
    engine.add_clip(&paths(cutters, SCALE)?);
    let mut parts = vec![];
    if !engine.execute(
        ClipType::Difference,
        FillRule::NonZero,
        &mut vec![],
        Some(&mut parts),
    ) {
        return Err("integer line clipping failed".into());
    }
    Ok(parts
        .into_iter()
        .map(|part| {
            part.into_iter()
                .map(|p| (p.x as f64 / SCALE, p.y as f64 / SCALE))
                .collect()
        })
        .collect())
}

pub fn snap(rings: &[Ring], scale: f64) -> Result<(Vec<Ring>, usize), String> {
    let mut collapsed = 0;
    let mut kept = vec![];
    for ring in rings {
        let mut p = path(&ring.0, scale)?;
        let signed = p
            .iter()
            .zip(p.iter().cycle().skip(1))
            .try_fold(0i128, |sum, (a, b)| {
                sum.checked_add(a.x as i128 * b.y as i128 - b.x as i128 * a.y as i128)
            });
        // Nonzero signed area proves that some nonzero-fill area remains.
        // Zero-area self crossings still need an actual fill operation.
        if p.len() < 3
            || (!signed.is_some_and(|value| value != 0)
                && resolve(&vec![p.clone()], &vec![], ClipType::Union, scale)?.is_empty())
        {
            collapsed += 1;
        } else {
            if signed.map_or_else(|| area(&p) < 0., |value| value < 0) != !ring.1 {
                p.reverse();
            }
            kept.push(p);
        }
    }
    Ok((resolve(&kept, &vec![], ClipType::Union, scale)?, collapsed))
}

/// Stroked paths per spatial bucket. Clipper's offset plus union grows faster
/// than linearly in the number of paths, so a large group is offset in buckets
/// and merged once; the region is the same, only the merge order changes.
const STROKE_BUCKET_TARGET: usize = 4;
/// Below this size bucketing cannot pay for the extra merge.
const STROKE_BUCKET_MIN_PATHS: usize = 256;
const STROKE_BUCKET_MAX_SIDE: usize = 64;

/// Split paths into a uniform grid of buckets, keyed by path bounding-box centre.
/// A path belongs to exactly one bucket, so the union over buckets is unchanged.
fn bucket<'a>(lines: &'a [Vec<Point>]) -> Vec<Vec<&'a [Point]>> {
    let count = lines.len();
    let side = if count < STROKE_BUCKET_MIN_PATHS {
        1
    } else {
        ((count as f64 / STROKE_BUCKET_TARGET as f64).sqrt().ceil() as usize)
            .clamp(2, STROKE_BUCKET_MAX_SIDE)
    };
    if side < 2 {
        return vec![lines.iter().map(Vec::as_slice).collect()];
    }
    let centre = |points: &Vec<Point>| -> Point {
        let (mut min_x, mut min_y) = (f64::MAX, f64::MAX);
        let (mut max_x, mut max_y) = (f64::MIN, f64::MIN);
        for &(x, y) in points {
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x);
            max_y = max_y.max(y);
        }
        ((min_x + max_x) / 2., (min_y + max_y) / 2.)
    };
    let centres: Vec<Point> = lines.iter().map(centre).collect();
    let bounds = centres.iter().fold(
        (f64::MAX, f64::MAX, f64::MIN, f64::MIN),
        |(min_x, min_y, max_x, max_y), &(x, y)| {
            (min_x.min(x), min_y.min(y), max_x.max(x), max_y.max(y))
        },
    );
    let width = (bounds.2 - bounds.0).max(f64::MIN_POSITIVE);
    let height = (bounds.3 - bounds.1).max(f64::MIN_POSITIVE);
    let mut buckets = vec![vec![]; side * side];
    for (points, &(x, y)) in lines.iter().zip(&centres) {
        let column = (((x - bounds.0) / width) * side as f64) as usize;
        let row = (((y - bounds.1) / height) * side as f64) as usize;
        buckets[row.min(side - 1) * side + column.min(side - 1)].push(points.as_slice());
    }
    buckets
}

pub fn stroke(lines: &[Vec<Point>], radius: f64, sagitta: f64) -> Result<Vec<Ring>, String> {
    if !radius.is_finite() || !sagitta.is_finite() || sagitta <= 0. {
        return Err("invalid stroke radius or sagitta".into());
    }
    if radius <= 0. {
        return Ok(vec![]);
    }
    let delta = (radius * SCALE).round_ties_even();
    let tolerance = (sagitta * SCALE).max(1.);
    let mut merged = vec![];
    let mut groups = 0;
    for bucket in bucket(lines) {
        if bucket.is_empty() {
            continue;
        }
        groups += 1;
        let mut offsetter = ClipperOffset::new_default();
        offsetter.set_arc_tolerance(tolerance);
        for points in bucket {
            let closed = points.len() >= 4
                && path(&[points[0], points[points.len() - 1]], SCALE)?.len() == 1;
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
        let mut solution = vec![];
        offsetter.execute(delta, &mut solution);
        merged.extend(resolve(&solution, &vec![], ClipType::Union, SCALE)?);
    }
    if groups <= 1 {
        Ok(merged)
    } else {
        resolve(&paths(&merged, SCALE)?, &vec![], ClipType::Union, SCALE)
    }
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
