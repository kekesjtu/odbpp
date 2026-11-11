//! Whole-ring spatial filtering preserves enclosing contours and island/hole parity.
use crate::{
    document::Polygon,
    geos::Result,
    raster::{Axis, PreparedRaster},
};
type Edge = (f64, f64, f64, f64);
struct Ring {
    bounds: [f64; 4],
    edges: Vec<Edge>,
}
struct Index(Vec<Ring>);
impl Index {
    fn new(polygons: &[Polygon]) -> Self {
        let mut rings: Vec<_> = polygons
            .iter()
            .map(|ring| {
                let bounds = [
                    ring.points
                        .iter()
                        .map(|p| p.0)
                        .fold(f64::INFINITY, f64::min),
                    ring.points
                        .iter()
                        .map(|p| p.1)
                        .fold(f64::INFINITY, f64::min),
                    ring.points
                        .iter()
                        .map(|p| p.0)
                        .fold(f64::NEG_INFINITY, f64::max),
                    ring.points
                        .iter()
                        .map(|p| p.1)
                        .fold(f64::NEG_INFINITY, f64::max),
                ];
                let edges = ring
                    .points
                    .iter()
                    .zip(ring.points.iter().cycle().skip(1))
                    .filter(|(a, b)| a.1 != b.1)
                    .map(|(&(x, y), &(a, b))| (x, y, a, b))
                    .collect();
                Ring { bounds, edges }
            })
            .collect();
        rings.sort_by(|a, b| a.bounds[0].total_cmp(&b.bounds[0]));
        Self(rings)
    }
    fn edges(&self, bounds: [f64; 4]) -> Vec<Edge> {
        let stop = self.0.partition_point(|r| r.bounds[0] <= bounds[2]);
        self.0[..stop]
            .iter()
            .filter(|r| {
                r.bounds[2] >= bounds[0] && r.bounds[1] <= bounds[3] && r.bounds[3] >= bounds[1]
            })
            .flat_map(|r| r.edges.iter().copied())
            .filter(|e| e.1.min(e.3) <= bounds[3] && e.1.max(e.3) > bounds[1])
            .collect()
    }
}
pub struct Materials {
    domain: Vec<Edge>,
    profile: Vec<Edge>,
    black: Vec<Index>,
}
impl Materials {
    pub fn new(domain: Vec<Edge>, profile: Vec<Edge>, black: &[&[Polygon]]) -> Self {
        Self {
            domain,
            profile,
            black: black.iter().map(|p| Index::new(p)).collect(),
        }
    }
    pub fn render(&self, x: Axis, y: Axis) -> Result<(Vec<u8>, usize)> {
        let bounds = [
            x.0 + (0.5 - 1e-9) * x.1,
            y.0 + (0.5 - 1e-9) * y.1,
            x.0 + (x.2 as f64 - 0.5 + 1e-9) * x.1,
            y.0 + (y.2 as f64 - 0.5 + 1e-9) * y.1,
        ];
        PreparedRaster::new(
            self.domain.clone(),
            Some(self.profile.clone()),
            self.black.iter().map(|i| i.edges(bounds)).collect(),
        )?
        .render_base(x, y)
    }
}
