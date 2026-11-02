use crate::geos::Result;
use serde::Deserialize;
use serde_json::{Value, json};
fn maximum() -> Vec<f64> {
    vec![5., 5.]
}
fn loss() -> f64 {
    0.05
}
fn materials() -> Value {
    json!({})
}
#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub px: Option<usize>,
    pub py: Option<usize>,
    pub grid_level: Option<String>,
    pub max_cell_mm: Vec<f64>,
    pub max_area_loss_ratio: f64,
    pub tolerance_mm: Option<f64>,
    pub materials: Value,
    pub materials_path: Option<String>,
    pub geos_library: String,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            px: None,
            py: None,
            grid_level: None,
            max_cell_mm: maximum(),
            max_area_loss_ratio: loss(),
            tolerance_mm: None,
            materials: materials(),
            materials_path: None,
            geos_library: String::new(),
        }
    }
}
impl Options {
    pub fn validate(&self) -> Result<()> {
        if self.px.is_some() != self.py.is_some() || self.px == Some(0) || self.py == Some(0) {
            return Err("px and py must both be positive".into());
        }
        if self
            .grid_level
            .as_deref()
            .is_some_and(|s| !matches!(s, "coarse" | "standard" | "fine"))
        {
            return Err("invalid grid level".into());
        }
        if self.max_cell_mm.is_empty()
            || self.max_cell_mm.len() > 2
            || self.max_cell_mm.iter().any(|v| !v.is_finite() || *v <= 0.)
        {
            return Err("maximum cell sizes require one or two finite positive numbers".into());
        }
        if !self.max_area_loss_ratio.is_finite()
            || !(0.0..1.0).contains(&self.max_area_loss_ratio)
            || self.tolerance_mm.is_some_and(|v| !v.is_finite() || v <= 0.)
        {
            return Err("invalid geometry accuracy budget".into());
        }
        if !self.materials.is_object() {
            return Err("materials must be a JSON object".into());
        }
        Ok(())
    }
    pub fn maximum(&self) -> [f64; 2] {
        [self.max_cell_mm[0], *self.max_cell_mm.last().unwrap()]
    }
}
