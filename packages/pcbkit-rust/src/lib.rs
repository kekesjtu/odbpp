use crate::geometry::{Point, curves};
use pyo3::{exceptions::PyValueError, prelude::*};

mod compose;
mod drawing;
mod geometry;
mod native;
mod parser;
mod symbols;

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

#[pymodule]
fn _pcbkit_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    native::register(m)?;
    m.add_function(wrap_pyfunction!(arc_vertices, m)?)?;
    m.add_function(wrap_pyfunction!(arc_points, m)?)?;
    Ok(())
}
