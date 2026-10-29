use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Rect = (f64, f64, f64, f64);
type Cell = (usize, usize, usize);
type Axes = (Vec<f64>, Vec<f64>);
type Layout = (Vec<Axes>, Vec<Cell>);
pub const MAX_AXIS_LINES: usize = 1_000_001;
pub const MAX_GRID_CELLS: usize = 5_000_000;

#[pyfunction]
pub fn build_grid(rectangles: Vec<Rect>, maximum: (f64, f64)) -> PyResult<Layout> {
    if !maximum.0.is_finite() || !maximum.1.is_finite() || maximum.0 <= 0. || maximum.1 <= 0. {
        return Err(PyValueError::new_err(
            "maximum cell sizes must be finite and positive",
        ));
    }
    if rectangles.iter().any(|r| {
        !r.0.is_finite()
            || !r.1.is_finite()
            || !r.2.is_finite()
            || !r.3.is_finite()
            || r.2 <= r.0
            || r.3 <= r.1
    }) {
        return Err(PyValueError::new_err(
            "region must have finite positive extent",
        ));
    }
    let mut sorted = rectangles.clone();
    sorted.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut active: Vec<Rect> = vec![];
    for r in sorted {
        active.retain(|p| p.2 > r.0);
        if active.iter().any(|p| p.3.min(r.3) > p.1.max(r.1)) {
            return Err(PyValueError::new_err(format!(
                "overlapping regions at {r:?}"
            )));
        }
        active.push(r);
    }
    let mut axes = vec![];
    let mut cells = vec![];
    for (rid, r) in rectangles.iter().enumerate() {
        let xs = axis(r.0, r.2, maximum.0)
            .map_err(|e| PyValueError::new_err(format!("resource limit: X region {rid}: {e}")))?;
        let ys = axis(r.1, r.3, maximum.1)
            .map_err(|e| PyValueError::new_err(format!("resource limit: Y region {rid}: {e}")))?;
        let (nx, ny) = (xs.len() - 1, ys.len() - 1);
        if nx.saturating_mul(ny) > MAX_GRID_CELLS.saturating_sub(cells.len()) {
            return Err(PyValueError::new_err(format!(
                "resource limit: region {rid}: more than {MAX_GRID_CELLS} modeled cells"
            )));
        }
        for j in 0..ny {
            for i in 0..nx {
                cells.push((rid, i, j));
            }
        }
        axes.push((xs, ys));
    }
    Ok((axes, cells))
}

fn axis(left: f64, right: f64, maximum: f64) -> Result<Vec<f64>, String> {
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

#[pyfunction]
pub fn allocate(left: f64, right: f64, maximum: f64) -> PyResult<Vec<f64>> {
    axis(left, right, maximum).map_err(PyValueError::new_err)
}
