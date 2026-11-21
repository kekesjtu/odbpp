//! Planar geometry owned by Rust; all operations use geo.
use crate::Result;
use geo::{
    Area, BooleanOps, BoundingRect, Buffer, InteriorPoint, LineString, MultiPolygon, Orient,
    Polygon, Relate, Validation, orient::Direction, unary_union,
};

pub type Point = (f64, f64);

#[derive(Clone)]
pub struct Shape(MultiPolygon<f64>);

impl Shape {
    pub fn polygon(shell: &[Point], holes: &[Vec<Point>]) -> Result<Self> {
        for ring in std::iter::once(shell).chain(holes.iter().map(Vec::as_slice)) {
            if ring.len() < 3 || ring.iter().any(|p| !p.0.is_finite() || !p.1.is_finite()) {
                return Err("polygon needs at least three finite points".into());
            }
        }
        let polygon = Polygon::new(
            LineString::from(shell.to_vec()),
            holes.iter().map(|p| LineString::from(p.clone())).collect(),
        )
        .orient(Direction::Default);
        Ok(Self(MultiPolygon(vec![polygon])))
    }

    pub fn rectangle(r: [f64; 4]) -> Self {
        Self(MultiPolygon(vec![
            geo::Rect::new(
                geo::coord! { x: r[0], y: r[1] },
                geo::coord! { x: r[2], y: r[3] },
            )
            .to_polygon(),
        ]))
    }

    pub fn union(shapes: &[Self]) -> Self {
        Self(unary_union(shapes.iter().map(|s| &s.0)))
    }

    pub fn rectangles(rectangles: &[[f64; 4]]) -> Self {
        Self::union(
            &rectangles
                .iter()
                .map(|r| Self::rectangle(*r))
                .collect::<Vec<_>>(),
        )
    }

    pub fn intersection(&self, other: &Self) -> Self {
        Self(self.0.intersection(&other.0))
    }

    pub fn difference(&self, other: &Self) -> Self {
        Self(self.0.difference(&other.0))
    }

    pub fn buffer(&self, distance: f64) -> Self {
        Self(self.0.buffer(distance))
    }

    pub fn covers(&self, other: &Self) -> bool {
        self.0.relate(&other.0).is_covers()
    }

    pub fn equals(&self, other: &Self) -> bool {
        self.0.relate(&other.0).is_equal_topo()
    }

    pub fn empty(&self) -> bool {
        self.0.0.is_empty()
    }

    pub fn valid(&self) -> bool {
        self.0.is_valid()
    }

    pub fn area(&self) -> f64 {
        self.0.unsigned_area()
    }

    pub fn bounds(&self) -> Result<[f64; 4]> {
        let r = self
            .0
            .bounding_rect()
            .ok_or("empty geometry has no bounds")?;
        Ok([r.min().x, r.min().y, r.max().x, r.max().y])
    }

    pub fn components(&self) -> Vec<Self> {
        self.0
            .0
            .iter()
            .cloned()
            .map(|p| Self(MultiPolygon(vec![p])))
            .collect()
    }

    pub fn polygons(&self) -> impl Iterator<Item = (bool, Vec<Point>)> + '_ {
        self.0.0.iter().flat_map(|p| {
            std::iter::once((true, p.exterior()))
                .chain(p.interiors().iter().map(|h| (false, h)))
                .map(|(island, ring)| {
                    let mut points: Vec<_> = ring.0.iter().map(|p| (p.x, p.y)).collect();
                    if points.len() > 1 && points.first() == points.last() {
                        points.pop();
                    }
                    (island, points)
                })
        })
    }

    pub fn holes(&self) -> Vec<Self> {
        self.0
            .0
            .iter()
            .flat_map(|p| p.interiors())
            .map(|h| {
                Self(MultiPolygon(vec![
                    Polygon::new(h.clone(), vec![]).orient(Direction::Default),
                ]))
            })
            .collect()
    }

    pub fn interior_point(&self) -> Result<Point> {
        self.0
            .interior_point()
            .map(|p| (p.x(), p.y()))
            .ok_or_else(|| "empty geometry has no interior point".into())
    }
}
