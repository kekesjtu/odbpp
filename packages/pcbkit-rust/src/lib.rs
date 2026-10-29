use pcb_geometry::{Point, Ring, boolean, curves};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
fn boolean_op(
    py: Python<'_>,
    operation: &str,
    subject: Vec<Ring>,
    clip: Vec<Ring>,
    scale: f64,
) -> PyResult<Vec<Ring>> {
    py.detach(|| boolean::operate(operation, &subject, &clip, scale))
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn snap_to_grid(py: Python<'_>, rings: Vec<Ring>, scale: f64) -> PyResult<(Vec<Ring>, usize)> {
    py.detach(|| boolean::snap(&rings, scale))
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn arc_vertices(
    center: Point,
    radius: f64,
    start: f64,
    sweep: f64,
    sagitta: f64,
    maximum: usize,
) -> PyResult<Vec<Point>> {
    curves::arc_vertices(center, radius, start, sweep, sagitta, maximum)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn arc_points(
    start: Point,
    end: Point,
    center: Point,
    clockwise: bool,
    sagitta: f64,
    maximum: usize,
) -> PyResult<Vec<Point>> {
    curves::arc_points(start, end, center, clockwise, sagitta, maximum)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn signed_area(points: Vec<Point>) -> f64 {
    curves::signed_area(&points)
}

#[pyfunction]
fn transform_points(points: Vec<Point>, angle: f64, mirrored: bool) -> Vec<Point> {
    curves::transform(&points, angle, mirrored)
}

#[pyfunction]
fn stroke_paths(
    py: Python<'_>,
    paths: Vec<Vec<Point>>,
    radius: f64,
    sagitta: f64,
) -> PyResult<Vec<Ring>> {
    py.detach(|| boolean::stroke(&paths, radius, sagitta))
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn offset(py: Python<'_>, rings: Vec<Ring>, delta: f64, sagitta: f64) -> PyResult<Vec<Ring>> {
    py.detach(|| boolean::offset(&rings, delta, sagitta))
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn sweep_paths(
    py: Python<'_>,
    patterns: Vec<Vec<Point>>,
    paths: Vec<Vec<Point>>,
) -> PyResult<Vec<Ring>> {
    py.detach(|| boolean::sweep(&patterns, &paths))
        .map_err(PyValueError::new_err)
}

#[pymodule]
fn _pcbkit_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(boolean_op, m)?)?;
    m.add_function(wrap_pyfunction!(snap_to_grid, m)?)?;
    m.add_function(wrap_pyfunction!(arc_vertices, m)?)?;
    m.add_function(wrap_pyfunction!(arc_points, m)?)?;
    m.add_function(wrap_pyfunction!(signed_area, m)?)?;
    m.add_function(wrap_pyfunction!(transform_points, m)?)?;
    m.add_function(wrap_pyfunction!(stroke_paths, m)?)?;
    m.add_function(wrap_pyfunction!(offset, m)?)?;
    m.add_function(wrap_pyfunction!(sweep_paths, m)?)?;
    Ok(())
}
