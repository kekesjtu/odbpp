//! Shared integer geometry, with coordinates in mm at the public boundary.
pub type Point = (f64, f64);
pub type Ring = (Vec<Point>, bool);

pub mod boolean;
pub mod curves;
