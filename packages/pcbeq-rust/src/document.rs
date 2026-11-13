//! Strict native reader for the shared intermediate contract.
use crate::{
    Result,
    geometry::{Point, Shape},
};
use serde::Deserialize;

fn board() -> String {
    "BOARD".into()
}
fn rigid() -> String {
    "RIGID".into()
}
fn default_form() -> String {
    "DEFAULT".into()
}
fn ready() -> String {
    "ready".into()
}
fn unknown() -> String {
    "unknown".into()
}
fn resolution() -> f64 {
    0.0001
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Polygon {
    pub island: bool,
    pub points: Vec<Point>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Domain {
    pub id: String,
    pub source: String,
    pub polygons: Vec<Polygon>,
}
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Extent {
    pub start_mm: f64,
    pub end_mm: f64,
    pub thickness_known: bool,
    #[serde(default = "unknown")]
    pub source: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Thickness {
    pub known: bool,
    pub source: String,
    input_value: Option<f64>,
    kind: Option<String>,
    unit: Option<String>,
    thickness_source: Option<String>,
    assumed_density_g_cm3: Option<f64>,
    #[serde(default)]
    spec_deviation: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Layer {
    pub name: String,
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default = "board")]
    pub context: String,
    pub row: u64,
    #[serde(default = "rigid")]
    form: String,
    #[serde(default = "default_form")]
    form_source: String,
    #[serde(default = "ready")]
    pub geometry_status: String,
    pub geometry_error: Option<String>,
    pub domain_id: Option<String>,
    pub polygons: Vec<Polygon>,
    pub z_mm: Option<Extent>,
    pub thickness: Option<Thickness>,
    pub start_layer: Option<String>,
    pub end_layer: Option<String>,
}
impl Layer {
    pub fn copper(&self) -> bool {
        matches!(self.kind.as_str(), "SIGNAL" | "POWER_GROUND" | "MIXED")
    }
    pub fn physical(&self) -> bool {
        self.context == "BOARD" && (self.copper() || self.kind == "DIELECTRIC")
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub name: String,
    pub profile_domain_id: Option<String>,
    pub domains: Vec<Domain>,
    pub layers: Vec<Layer>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Product {
    name: Option<String>,
    odb_version: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Source {
    path: String,
    job_root: String,
    input_units: String,
    product: Product,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Board {
    units: String,
    source: Option<Source>,
    #[serde(default = "resolution")]
    resolution_mm: f64,
    pub steps: Vec<Step>,
}
#[path = "document_validation.rs"]
mod validation;
use validation::require;

pub fn shape(polygons: &[Polygon]) -> Result<Shape> {
    let islands = polygons.iter().filter(|p| p.island).collect::<Vec<_>>();
    let shells = islands
        .iter()
        .map(|p| Shape::polygon(&p.points, &[]))
        .collect::<Result<Vec<_>>>()?;
    let mut holes = vec![vec![]; islands.len()];
    for p in polygons.iter().filter(|p| !p.island) {
        let hole = Shape::polygon(&p.points, &[])?;
        let mut parent = None;
        for (i, s) in shells.iter().enumerate() {
            if s.covers(&hole) {
                let area = s.area();
                if parent.is_none_or(|(_, a)| area < a) {
                    parent = Some((i, area));
                }
            }
        }
        holes[parent.ok_or("profile hole has no containing island")?.0].push(p.points.clone());
    }
    let geometry = Shape::union(
        &islands
            .iter()
            .zip(holes)
            .map(|(p, h)| Shape::polygon(&p.points, &h))
            .collect::<Result<Vec<_>>>()?,
    );
    require(
        geometry.valid(),
        "invalid profile topology; regenerate parser JSON",
    )?;
    Ok(geometry)
}
pub fn polygons(shape: &Shape) -> Vec<Polygon> {
    shape
        .polygons()
        .map(|(island, points)| Polygon { island, points })
        .collect()
}
