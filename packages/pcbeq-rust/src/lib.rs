//! Independent, file-based PCB material equivalence.
pub type Result<T> = std::result::Result<T, String>;

mod diagnostics;
mod document;
mod fast;
mod geometry;
mod grid;
mod native_materials;
mod native_mesh;
mod native_output;
mod native_publication;
mod native_sampling;
pub mod options;
pub mod pipeline;
mod planning;
mod raster;
mod raster_index;
mod rectangles;
mod region_raster;
mod sampling;
mod series_parallel;
mod thermal_network;
mod topology;
