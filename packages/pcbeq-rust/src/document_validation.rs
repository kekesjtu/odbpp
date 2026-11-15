use super::*;
use std::collections::{HashMap, HashSet};

pub(super) fn require(ok: bool, message: impl Into<String>) -> Result<()> {
    if ok { Ok(()) } else { Err(message.into()) }
}
fn validate_polygons(polygons: &[Polygon]) -> Result<()> {
    for p in polygons {
        require(p.points.len() >= 3, "polygon needs at least three points")?;
        require(
            p.points.iter().all(|p| p.0.is_finite() && p.1.is_finite()),
            "non-finite polygon coordinate",
        )?;
    }
    Ok(())
}
impl Board {
    pub fn validate(&self) -> Result<()> {
        require(self.units == "mm", "intermediate units must be mm")?;
        require(
            self.resolution_mm.is_finite() && self.resolution_mm > 0.,
            "resolution_mm must be positive",
        )?;
        require(
            self.steps.len() == 1,
            "exactly one physical step is required",
        )?;
        if let Some(s) = &self.source {
            require(
                matches!(s.input_units.as_str(), "MM" | "INCH"),
                "invalid source input_units",
            )?;
            let _ = (
                &s.path,
                &s.job_root,
                &s.product.name,
                &s.product.odb_version,
            );
        }
        let step = &self.steps[0];
        require(!step.name.is_empty(), "step name is empty")?;
        let mut profiles = HashSet::new();
        for d in &step.profiles {
            require(
                !d.id.is_empty() && profiles.insert(d.id.as_str()),
                "empty/duplicate profile id",
            )?;
            validate_polygons(&d.polygons)?;
        }
        let profile = step.profile_id.as_ref().ok_or("missing step profile")?;
        require(
            step.profiles.iter().any(|d| &d.id == profile),
            "profile_id must reference an existing profile",
        )?;
        let mut names = HashSet::new();
        let mut rows = HashSet::new();
        let mut copper = HashMap::new();
        for l in &step.layers {
            require(
                !l.name.is_empty() && names.insert(l.name.as_str()),
                "empty/duplicate layer name",
            )?;
            require(!l.kind.is_empty() && l.row > 0, "invalid layer type/row")?;
            require(
                rows.insert(l.row),
                format!("layer {:?}: duplicate matrix row {}", l.name, l.row),
            )?;
            require(l.context == "BOARD", "invalid layer context")?;
            require(
                matches!(l.geometry_status.as_str(), "ready" | "failed" | "omitted"),
                "invalid geometry_status",
            )?;
            require(
                l.geometry_status == "ready" || l.polygons.is_empty(),
                "failed/omitted geometry must not carry polygons",
            )?;
            require(
                l.geometry_status != "failed"
                    || l.geometry_error.as_ref().is_some_and(|s| !s.is_empty()),
                "failed geometry requires geometry_error",
            )?;
            validate_polygons(&l.polygons)?;
            if let Some(z) = &l.z_mm {
                require(l.kind != "DRILL", "DRILL must not carry z_mm")?;
                require(
                    z.start_mm.is_none_or(|v| v >= 0.) && z.thickness_mm.is_none_or(|v| v >= 0.),
                    "invalid absolute Z extent",
                )?;
            }
            if l.copper() && l.context == "BOARD" {
                copper.insert(l.name.as_str(), l.row);
            }
        }
        let mut assigned = HashSet::new();
        for profile in &step.profiles {
            for name in &profile.layers {
                require(
                    names.contains(name.as_str()),
                    format!("profile {:?}: unknown layer {name:?}", profile.id),
                )?;
                require(
                    assigned.insert(name.as_str()),
                    format!("layer {name:?}: multiple profile assignments"),
                )?;
            }
        }
        for l in &step.layers {
            require(
                l.kind == "DRILL" || (l.start_layer.is_none() && l.end_layer.is_none()),
                format!(
                    "layer {:?}: start_layer/end_layer require a DRILL layer",
                    l.name
                ),
            )?;
            for endpoint in [&l.start_layer, &l.end_layer].into_iter().flatten() {
                require(
                    copper.contains_key(endpoint.as_str()),
                    format!(
                        "layer {:?}: endpoint {endpoint:?} must reference an existing BOARD copper layer",
                        l.name
                    ),
                )?;
            }
            if let (Some(a), Some(b)) = (&l.start_layer, &l.end_layer) {
                require(
                    copper[a.as_str()] < copper[b.as_str()],
                    format!(
                        "layer {:?}: drill endpoints must follow matrix rows",
                        l.name
                    ),
                )?;
            }
        }
        Ok(())
    }
}
