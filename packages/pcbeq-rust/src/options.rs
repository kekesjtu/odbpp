use crate::Result;
use clap::ValueEnum;
use serde_json::Value;
use std::num::NonZeroUsize;

#[derive(Clone, Copy, Debug, Default, ValueEnum)]
pub enum Mode {
    /// 2DVTN, a two-dimensional pixel thermal resistance network.
    #[default]
    #[value(name = "1")]
    TwoDimensional,
    /// SPTRA, a series-parallel approximation of the pixel thermal resistances.
    #[value(name = "2")]
    Sptra,
}

pub struct Options {
    pub pixels_per_cell_axis: NonZeroUsize,
    pub grid_level: Option<String>,
    pub max_cell_mm: Vec<f64>,
    pub max_area_error_ratio: f64,
    pub materials: Value,
    pub materials_path: Option<String>,
    pub mode: Mode,
}
impl Options {
    pub fn validate(&self) -> Result<()> {
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
        if !self.max_area_error_ratio.is_finite()
            || !(0.0..1.0).contains(&self.max_area_error_ratio)
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
