use pcb_geometry::{Ring, boolean};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

mod document;
mod fast;
mod geos;
mod grid;
mod native_materials;
mod native_mesh;
mod native_output;
mod native_publication;
mod native_sampling;
mod options;
mod pipeline;
mod planning;
mod raster;
mod raster_index;
mod rectangles;
mod region_raster;
mod sampling;
mod topology;

#[pyfunction]
fn run_materialmap(
    py: Python<'_>,
    input: String,
    output: String,
    options_json: String,
) -> PyResult<String> {
    py.detach(move || {
        let options: options::Options =
            serde_json::from_str(&options_json).map_err(|e| e.to_string())?;
        let summary = pipeline::run(
            std::path::Path::new(&input),
            std::path::Path::new(&output),
            options,
        )?;
        serde_json::to_string(&summary).map_err(|e| e.to_string())
    })
    .map_err(PyValueError::new_err)
}

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
    m.add_function(wrap_pyfunction!(raster::rasterize, m)?)?;
    m.add_function(wrap_pyfunction!(sampling::integrate, m)?)?;
    m.add_function(wrap_pyfunction!(run_materialmap, m)?)?;
    Ok(())
}
