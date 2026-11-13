use crate::stroke_groups::{self, StrokePath};
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
    Ok(rings_from_tree(&tree, scale))
}

fn rings_from_tree(tree: &PolyTree64, scale: f64) -> Vec<Ring> {
    let mut rings = Vec::with_capacity(tree.nodes.len().saturating_sub(1));
    for i in 1..tree.nodes.len() {
        if let Some(ring) = ring_from_path(tree.nodes[i].polygon(), tree.is_hole(i), scale) {
            rings.push(ring);
        }
    }
    rings
}

fn ring_from_path(path: &Path64, hole: bool, scale: f64) -> Option<Ring> {
    let mut p: Vec<Point> = path
        .iter()
        .map(|p| (p.x as f64 / scale, p.y as f64 / scale))
        .collect();
    if p.len() < 3 || signed_area(&p).abs() <= 1e-18 {
        return None;
    }
    if (signed_area(&p) < 0.) != !hole {
        p.reverse();
    }
    Some((p, hole))
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

/// A positive convex contour has no self-intersections to repair. Requiring a
/// single turn of its edge directions excludes multiply wound star polygons.
fn convex_island(p: &Path64) -> bool {
    if p.len() < 3 {
        return false;
    }
    let mut turns = 0;
    let mut bent = false;
    for i in 0..p.len() {
        let a = p[i];
        let b = p[(i + 1) % p.len()];
        let c = p[(i + 2) % p.len()];
        let u = (b.x as i128 - a.x as i128, b.y as i128 - a.y as i128);
        let v = (c.x as i128 - b.x as i128, c.y as i128 - b.y as i128);
        if u == (0, 0) || v == (0, 0) {
            return false;
        }
        let Some(cross) =
            u.0.checked_mul(v.1)
                .and_then(|a| u.1.checked_mul(v.0).and_then(|b| a.checked_sub(b)))
        else {
            return false;
        };
        let dot =
            u.0.checked_mul(v.0)
                .and_then(|a| u.1.checked_mul(v.1).and_then(|b| a.checked_add(b)));
        if cross < 0 || (cross == 0 && !dot.is_some_and(|d| d > 0)) {
            return false;
        }
        bent |= cross > 0;
        let upper = |(x, y): (i128, i128)| y > 0 || (y == 0 && x > 0);
        turns += usize::from(upper(u) != upper(v));
    }
    bent && turns == 2
}

/// This certificate is intentionally conservative: positive convex contours
/// with disjoint X projections cannot intersect, nest or need another union.
fn separate_convex_islands(polygons: &Paths64) -> bool {
    let mut spans = Vec::with_capacity(polygons.len());
    for p in polygons {
        if !convex_island(p) {
            return false;
        }
        spans.push((
            p.iter().map(|p| p.x).min().unwrap(),
            p.iter().map(|p| p.x).max().unwrap(),
        ));
    }
    spans.sort_unstable();
    spans.windows(2).all(|pair| pair[0].1 < pair[1].0)
}

pub fn stroke(lines: &[Vec<Point>], radius: f64, sagitta: f64) -> Result<Vec<Ring>, String> {
    if !radius.is_finite() || !sagitta.is_finite() || sagitta <= 0. {
        return Err("invalid stroke radius or sagitta".into());
    }
    if radius <= 0. {
        return Ok(vec![]);
    }
    let mut centerlines = Vec::with_capacity(lines.len());
    for points in lines {
        let closed =
            points.len() >= 4 && path(&[points[0], points[points.len() - 1]], SCALE)?.len() == 1;
        let p = path(points, SCALE)?;
        if p.len() < if closed { 3 } else { 2 } {
            continue;
        }
        centerlines.push(StrokePath { points: p, closed });
    }
    let groups = stroke_groups::disjoint_groups(&centerlines, (radius * SCALE).round_ties_even());
    if groups.is_empty() {
        return Ok(vec![]);
    }
    let mut offsetter = ClipperOffset::new_default();
    offsetter.set_arc_tolerance((sagitta * SCALE).max(1.));
    let mut tree = PolyTree64::new();
    let mut result = vec![];
    for group in &groups {
        offsetter.clear();
        let single_segment = group.len() == 1 && centerlines[group[0]].points.len() == 2;
        let mut start = 0;
        while start < group.len() {
            let closed = centerlines[group[start]].closed;
            let mut end = start + 1;
            while end < group.len() && centerlines[group[end]].closed == closed {
                end += 1;
            }
            let paths = group[start..end]
                .iter()
                .map(|&index| std::mem::take(&mut centerlines[index].points))
                .collect();
            offsetter.add_paths(
                &paths,
                JoinType::Round,
                if closed {
                    EndType::Joined
                } else {
                    EndType::Round
                },
            );
            start = end;
        }
        // Retain each primitive's caps, tessellation and relative input order.
        // All potentially intersecting strokes are regularised together.
        if single_segment {
            // A straight round stroke is convex and needs no intersection
            // repair. Its island/hole tree can be delivered directly.
            offsetter.execute_tree((radius * SCALE).round_ties_even(), &mut tree);
            result.extend(rings_from_tree(&tree, SCALE));
        } else {
            // Rounded intersections can introduce contacts and collinear
            // breakpoints. Preserve this canonicalisation, but keep it local.
            let mut polygons = vec![];
            offsetter.execute((radius * SCALE).round_ties_even(), &mut polygons);
            if separate_convex_islands(&polygons) {
                result.extend(
                    polygons
                        .iter()
                        .filter_map(|p| ring_from_path(p, false, SCALE)),
                );
            } else {
                result.extend(resolve(&polygons, &vec![], ClipType::Union, SCALE)?);
            }
        }
    }
    // The groups have disjoint conservative stroke envelopes. Their positive
    // regions can be concatenated without another boolean or quantisation.
    Ok(result)
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

    fn independent_strokes(lines: &[Vec<Point>], radius: f64, sagitta: f64) -> Vec<Ring> {
        let mut offsetter = ClipperOffset::new_default();
        offsetter.set_arc_tolerance((sagitta * SCALE).max(1.));
        for line in lines {
            let closed = line.len() >= 4
                && path(&[line[0], line[line.len() - 1]], SCALE).unwrap().len() == 1;
            let points = path(line, SCALE).unwrap();
            if points.len() >= if closed { 3 } else { 2 } {
                offsetter.add_path(
                    &points,
                    JoinType::Round,
                    if closed {
                        EndType::Joined
                    } else {
                        EndType::Round
                    },
                );
            }
        }
        let mut raw = vec![];
        offsetter.execute((radius * SCALE).round_ties_even(), &mut raw);
        resolve(&raw, &vec![], ClipType::Union, SCALE).unwrap()
    }

    fn assert_same_strokes(lines: &[Vec<Point>], radius: f64, sagitta: f64) {
        let actual = stroke(lines, radius, sagitta).unwrap();
        let expected = independent_strokes(lines, radius, sagitta);
        let difference = resolve(
            &paths(&actual, SCALE).unwrap(),
            &paths(&expected, SCALE).unwrap(),
            ClipType::Xor,
            SCALE,
        )
        .unwrap();
        assert!(
            difference.is_empty(),
            "stroke geometry changed: radius={radius}, sagitta={sagitta}, diff={difference:?}"
        );
    }

    #[test]
    fn chain_strokes_keep_original_caps_at_bends_and_branches() {
        let cases = [
            vec![
                vec![(1., 0.), (0., 0.)],
                vec![(1., 0.), (1., 1.)],
                vec![(1., 1.), (0., 0.)],
            ],
            vec![
                vec![(-1., 0.), (0., 0.)],
                vec![(0., 0.), (1., 0.)],
                vec![(0., 0.), (0., 1.)],
            ],
            vec![
                vec![(0., 0.), (1., 0.001)],
                vec![(1., 0.001), (0.2, 0.002)],
                vec![(0.2, 0.002), (0., 0.)],
            ],
            vec![
                vec![(0., 0.), (1., 1.)],
                vec![(0., 1.), (1., 0.)],
                vec![(1., 0.), (2., 1.)],
            ],
            vec![
                vec![(0., 0.), (1., 0.), (1., 1.), (0., 0.)],
                vec![(2., 0.), (3., 0.)],
            ],
            vec![
                vec![(0., 0.), (1., 0.)],
                vec![(1., 0.), (0., 0.)],
                vec![(4., 4.), (4., 4.)],
            ],
        ];
        for lines in &cases {
            for (radius, sagitta) in [
                (0.0000004, 0.005),
                (0.0000635, 0.005),
                (0.075, 0.005),
                (0.5, 0.001),
            ] {
                assert_same_strokes(lines, radius, sagitta);
            }
        }
    }

    #[test]
    fn chain_strokes_keep_discretized_arcs_and_nested_closed_paths() {
        let mut outer = vec![(3., 0.)];
        outer.extend(
            crate::curves::arc_points((3., 0.), (3., 0.), (0., 0.), true, 0.001, 4096).unwrap(),
        );
        let mut inner = vec![(1., 0.)];
        inner.extend(
            crate::curves::arc_points((1., 0.), (1., 0.), (0., 0.), false, 0.001, 4096).unwrap(),
        );
        let mut arc = vec![(5., 0.)];
        arc.extend(
            crate::curves::arc_points((5., 0.), (6., 1.), (6., 0.), true, 0.001, 4096).unwrap(),
        );
        assert_same_strokes(&[outer, inner, arc, vec![(6., 1.), (7., 2.)]], 0.1, 0.001);
    }

    #[test]
    fn spatial_partition_preserves_crossings_overlaps_and_quantized_endpoints() {
        let mut lines = vec![];
        for x in 0..4 {
            for y in 0..4 {
                let (x, y) = (x as f64 * 3., y as f64 * 3.);
                lines.extend([
                    vec![(x, y), (x + 1., y + 1.)],
                    vec![(x + 1., y), (x, y + 1.)],
                    vec![(x, y + 1.), (x + 1., y + 1.)],
                    vec![(x + 1.0000001, y + 1.), (x + 2., y + 0.01)],
                ]);
            }
        }
        for (radius, sagitta) in [(0.0000635, 0.005), (0.075, 0.005), (0.8, 0.001)] {
            assert_same_strokes(&lines, radius, sagitta);
        }
    }

    #[test]
    fn convex_certificate_rejects_holes_contacts_and_multiple_winding() {
        let polygon =
            |vertices: &[(i64, i64)]| vertices.iter().map(|&(x, y)| Point64::new(x, y)).collect();
        let left: Path64 = polygon(&[(0, 0), (10, 0), (10, 10), (0, 10)]);
        let right = polygon(&[(20, 0), (30, 0), (30, 10), (20, 10)]);
        assert!(separate_convex_islands(&vec![left.clone(), right]));
        assert!(!separate_convex_islands(&vec![left.clone(), left.clone()]));
        assert!(!convex_island(&left.iter().copied().rev().collect()));
        assert!(!convex_island(&polygon(&[
            (0, 10),
            (-6, -8),
            (10, 3),
            (-10, 3),
            (6, -8)
        ])));
        for radius in [0.0000635, 0.075, 0.5] {
            assert_same_strokes(
                &[vec![(0., 0.), (0., 1.)], vec![(3., 0.), (3., 1.)]],
                radius,
                0.005,
            );
        }
    }

    #[test]
    fn disjoint_x_ranges_keep_shared_scanbeam_rounding() {
        let lines: Vec<Vec<Point>> = [
            [(1.725031496063, -0.222141732283), (1.725, -0.221826771654)],
            [
                (1.759708661417, -0.220062992126),
                (1.760149606299, -0.219811023622),
            ],
            [
                (1.760149606299, -0.219811023622),
                (1.760622047244, -0.219590551181),
            ],
        ]
        .into_iter()
        .map(|line| {
            line.into_iter()
                .map(|(x, y)| (x * 25.4, y * 25.4))
                .collect()
        })
        .collect();
        assert_same_strokes(&lines, 5.906 * 0.0254 / 2., 0.005);
    }
}
