//! Exact one-sided affine limits for the common Y intervals of an X strip.
use pcb_geometry::Point;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Edge = (f64, f64, f64, f64);
type Span = (f64, f64);

fn intersect(a: &[Span], b: &[Span]) -> Vec<Span> {
    let (mut i, mut j) = (0, 0);
    let mut out = vec![];
    while i < a.len() && j < b.len() {
        let (lo, hi) = (a[i].0.max(b[j].0), a[i].1.min(b[j].1));
        if hi > lo + 1e-9 {
            out.push((lo, hi));
        }
        if a[i].1 < b[j].1 {
            i += 1;
        } else {
            j += 1;
        }
    }
    out
}

#[pyfunction]
pub fn envelope(rings: Vec<Vec<Point>>, a: f64, b: f64) -> PyResult<Vec<Span>> {
    if !a.is_finite()
        || !b.is_finite()
        || b <= a
        || rings
            .iter()
            .flatten()
            .any(|p| !p.0.is_finite() || !p.1.is_finite())
    {
        return Err(PyValueError::new_err(
            "non-finite profile or invalid X interval",
        ));
    }
    let mut events = vec![a, b];
    let mut edges: Vec<Edge> = vec![];
    for ring in &rings {
        for (&(x0, y0), &(x1, y1)) in ring.iter().zip(ring.iter().cycle().skip(1)) {
            if a < x0 && x0 < b {
                events.push(x0);
            }
            if x0 != x1 && x0.min(x1) <= b && x0.max(x1) >= a {
                edges.push((x0, y0, x1, y1));
            }
        }
    }
    events.sort_by(f64::total_cmp);
    events.dedup();
    let mut spans: Option<Vec<Span>> = None;
    for event in events.windows(2) {
        let (left, right) = (event[0], event[1]);
        let active: Vec<_> = edges
            .iter()
            .filter(|e| e.0.min(e.2) <= left && e.0.max(e.2) >= right)
            .collect();
        let at = |x| {
            let mut ys: Vec<f64> = active
                .iter()
                .map(|&&(x0, y0, x1, y1)| {
                    if x == x0 {
                        y0
                    } else if x == x1 {
                        y1
                    } else {
                        y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                    }
                })
                .collect();
            ys.sort_by(f64::total_cmp);
            ys
        };
        let (ys, zs) = (at(left), at(right));
        let value: Vec<Span> = ys
            .as_chunks::<2>()
            .0
            .iter()
            .zip(zs.as_chunks::<2>().0.iter())
            .filter_map(|(s, t)| {
                let (lo, hi) = (s[0].max(t[0]), s[1].min(t[1]));
                (hi > lo + 1e-8).then_some((lo, hi))
            })
            .collect();
        spans = Some(match spans {
            None => value,
            Some(old) => intersect(&old, &value),
        });
    }
    Ok(spans
        .unwrap_or_default()
        .into_iter()
        .filter(|s| s.1 > s.0 + 1e-8)
        .collect())
}
