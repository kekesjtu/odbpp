use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashSet;

type Rect = (f64, f64, f64, f64);
type Cell = (usize, usize, usize);
type Layout = (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<Cell>);
pub const MAX_AXIS_LINES: usize = 2_000_000;
pub const MAX_GRID_CELLS: usize = 5_000_000;

#[pyfunction]
pub fn build_grid(rectangles: Vec<Rect>, maximum: (f64, f64)) -> PyResult<Layout> {
    if !maximum.0.is_finite() || !maximum.1.is_finite() || maximum.0 <= 0. || maximum.1 <= 0. {
        return Err(PyValueError::new_err(
            "maximum cell sizes must be finite and positive",
        ));
    }
    if rectangles.iter().any(|r| r.2 <= r.0 || r.3 <= r.1) {
        return Err(PyValueError::new_err("region must have positive area"));
    }
    let mut axes = vec![];
    let mut forced = vec![];
    for direction in 0..2 {
        let mut values: Vec<f64> = rectangles
            .iter()
            .flat_map(|r| {
                if direction == 0 {
                    [r.0, r.2]
                } else {
                    [r.1, r.3]
                }
            })
            .collect();
        if values.iter().any(|v| !v.is_finite()) {
            return Err(PyValueError::new_err("non-finite region boundary"));
        }
        values.sort_by(f64::total_cmp);
        values.dedup();
        let mut axis = vec![];
        for pair in values.windows(2) {
            let part = allocate(
                pair[0],
                pair[1],
                if direction == 0 { maximum.0 } else { maximum.1 },
            )?;
            if axis.len() + part.len() > MAX_AXIS_LINES {
                return Err(PyValueError::new_err(
                    "resource limit: more than 2000000 global axis lines",
                ));
            }
            axis.extend_from_slice(&part[..part.len() - 1]);
        }
        if let Some(last) = values.last() {
            axis.push(*last);
        }
        axes.push(axis);
        forced.push(values);
    }
    let (xs, ys) = (&axes[0], &axes[1]);
    let mut cells = vec![];
    let mut occupied = HashSet::new();
    for (rid, r) in rectangles.iter().enumerate() {
        if r.2 <= r.0 || r.3 <= r.1 {
            return Err(PyValueError::new_err("region must have positive area"));
        }
        let index = |lines: &Vec<f64>, value| {
            lines
                .binary_search_by(|v| {
                    if *v == value {
                        std::cmp::Ordering::Equal
                    } else {
                        v.total_cmp(&value)
                    }
                })
                .unwrap()
        };
        let (i0, i1, j0, j1) = (
            index(xs, r.0),
            index(xs, r.2),
            index(ys, r.1),
            index(ys, r.3),
        );
        let additional = (i1 - i0).saturating_mul(j1 - j0);
        if additional > MAX_GRID_CELLS.saturating_sub(cells.len()) {
            return Err(PyValueError::new_err(
                "resource limit: more than 5000000 modeled cells",
            ));
        }
        for j in j0..j1 {
            for i in i0..i1 {
                if !occupied.insert((i, j)) {
                    return Err(PyValueError::new_err(format!(
                        "overlapping regions at global Cartesian cell ({i},{j})"
                    )));
                }
                cells.push((rid, i, j));
            }
        }
    }
    Ok((
        axes.remove(0),
        axes.remove(0),
        forced.remove(0),
        forced.remove(0),
        cells,
    ))
}

#[pyfunction]
pub fn allocate(left: f64, right: f64, maximum: f64) -> PyResult<Vec<f64>> {
    let ratio = (right - left) / maximum;
    if !left.is_finite()
        || !right.is_finite()
        || right <= left
        || !maximum.is_finite()
        || maximum <= 0.
    {
        return Err(PyValueError::new_err(
            "axis endpoints and maximum must be finite, increasing and positive",
        ));
    }
    if !ratio.is_finite() || ratio > 1_000_000. {
        return Err(PyValueError::new_err(format!(
            "resource limit: interval [{left}, {right}] mm requires unrepresentable count {ratio}"
        )));
    }
    let n = ((ratio - 1e-10).ceil() as usize).max(1);
    let mut out = vec![left];
    out.extend((1..n).map(|i| left + (right - left) * i as f64 / n as f64));
    out.push(right);
    Ok(out)
}
