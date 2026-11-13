//! Conservative scanbeam groups retain every original stroke primitive.
use clipper2_rust::Path64;

pub(crate) struct StrokePath {
    pub points: Path64,
    pub closed: bool,
}

/// Keep overlapping scanbeam ranges together even when X bounds are disjoint:
/// integer intersection rounding also depends on the sweep's shared Y events.
pub(crate) fn disjoint_groups(paths: &[StrokePath], radius: f64) -> Vec<Vec<usize>> {
    match paths.len() {
        0 => return vec![],
        1 => return vec![vec![0]],
        _ => {}
    }
    let mut spans: Vec<_> = paths
        .iter()
        .enumerate()
        .map(|(index, path)| {
            let mut lo = i64::MAX;
            let mut hi = i64::MIN;
            let mut magnitude = 0u64;
            for point in &path.points {
                lo = lo.min(point.y);
                hi = hi.max(point.y);
                magnitude = magnitude
                    .max(point.x.unsigned_abs())
                    .max(point.y.unsigned_abs());
            }
            // Include offset rounding and floating conversion error throughout the
            // supported coordinate range. The margin only joins additional groups.
            let margin =
                (radius.abs().ceil() + 1. + (magnitude as f64 + radius.abs()) * f64::EPSILON * 4.)
                    .ceil() as i64;
            (lo.saturating_sub(margin), hi.saturating_add(margin), index)
        })
        .collect();
    spans.sort_unstable_by_key(|span| span.0);
    let mut groups: Vec<(i64, Vec<usize>)> = vec![];
    for (lo, hi, index) in spans {
        if let Some((end, group)) = groups.last_mut().filter(|(end, _)| lo <= *end) {
            *end = (*end).max(hi);
            group.push(index);
        } else {
            groups.push((hi, vec![index]));
        }
    }
    // Match the descending scanbeam order of a single global operation.
    groups
        .into_iter()
        .rev()
        .map(|(_, mut indices)| {
            indices.sort_unstable();
            indices
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use clipper2_rust::Point64;

    fn segment(a: (i64, i64), b: (i64, i64)) -> StrokePath {
        StrokePath {
            points: vec![Point64::new(a.0, a.1), Point64::new(b.0, b.1)],
            closed: false,
        }
    }

    #[test]
    fn shared_scanbeams_stay_together_and_disjoint_y_ranges_split() {
        let paths = vec![
            segment((0, 0), (100, 100)),
            segment((0, 100), (100, 0)),
            segment((100, 0), (200, 100)),
            segment((1000, -1000), (1100, -1000)),
        ];
        assert_eq!(disjoint_groups(&paths, 1.), vec![vec![0, 1, 2], vec![3]]);
    }

    #[test]
    fn width_and_interior_vertices_bound_the_scanbeam_range() {
        let paths = vec![
            StrokePath {
                points: vec![(0, 0), (100, 0), (100, 100)]
                    .into_iter()
                    .map(|(x, y)| Point64::new(x, y))
                    .collect(),
                closed: false,
            },
            segment((90, 20), (110, 20)),
            segment((200, -200), (300, -200)),
        ];
        assert_eq!(disjoint_groups(&paths, 1.), vec![vec![0, 1], vec![2]]);
        assert_eq!(disjoint_groups(&paths, 100.), vec![vec![0, 1, 2]]);
    }

    #[test]
    fn a_group_retains_original_primitive_order() {
        let paths = vec![
            segment((1, 0), (2, 0)),
            segment((0, 0), (1, 0)),
            segment((3, 0), (2, 0)),
        ];
        assert_eq!(disjoint_groups(&paths, 1.), vec![vec![0, 1, 2]]);
    }
}
