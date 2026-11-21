//! Parser geometry, with coordinates in mm at the module boundary.
pub(super) type Point = (f64, f64);
pub(super) type Ring = (Vec<Point>, bool);

pub(super) mod boolean;
pub(super) mod curves;
