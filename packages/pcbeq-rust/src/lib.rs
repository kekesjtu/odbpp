use pcb_geometry::{Ring, boolean};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

mod grid;
mod profile;
mod raster;
mod sampling;

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

#[pymodule]
fn _pcbeq_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("MAX_AXIS_LINES", grid::MAX_AXIS_LINES)?;
    m.add("MAX_GRID_CELLS", grid::MAX_GRID_CELLS)?;
    m.add("MAX_RASTER_PIXELS", raster::MAX_RASTER_PIXELS)?;
    m.add_function(wrap_pyfunction!(boolean_op, m)?)?;
    m.add_function(wrap_pyfunction!(grid::allocate, m)?)?;
    m.add_function(wrap_pyfunction!(grid::build_grid, m)?)?;
    m.add_function(wrap_pyfunction!(profile::envelope, m)?)?;
    m.add_function(wrap_pyfunction!(raster::rasterize, m)?)?;
    m.add_function(wrap_pyfunction!(sampling::integrate, m)?)?;
    Ok(())
}
