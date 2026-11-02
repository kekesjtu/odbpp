use super::*;
use std::collections::HashSet;

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
        let mut domains = HashSet::new();
        for d in &step.domains {
            require(
                !d.id.is_empty() && domains.insert(d.id.as_str()),
                "empty/duplicate domain id",
            )?;
            require(
                matches!(d.source.as_str(), "step_profile" | "layer_profile"),
                "invalid domain source",
            )?;
            validate_polygons(&d.polygons)?;
        }
        let profile = step
            .profile_domain_id
            .as_ref()
            .ok_or("missing step_profile domain")?;
        require(
            step.domains
                .iter()
                .any(|d| &d.id == profile && d.source == "step_profile"),
            "profile_domain_id must reference a step_profile domain",
        )?;
        let mut names = HashSet::new();
        let mut copper = HashSet::new();
        for l in &step.layers {
            require(
                !l.name.is_empty() && names.insert(l.name.as_str()),
                "empty/duplicate layer name",
            )?;
            require(!l.kind.is_empty() && l.row > 0, "invalid layer type/row")?;
            require(
                matches!(l.context.as_str(), "BOARD" | "MISC"),
                "invalid layer context",
            )?;
            require(
                matches!(l.form.as_str(), "RIGID" | "FLEX"),
                "invalid layer form",
            )?;
            require(
                matches!(
                    l.form_source.as_str(),
                    "DECLARED" | "FLEX_SUBTYPE" | "DEFAULT"
                ),
                "invalid form_source",
            )?;
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
            if let Some(d) = &l.domain_id {
                require(
                    domains.contains(d.as_str()),
                    format!("layer {:?}: unknown domain_id {d:?}", l.name),
                )?;
            }
            validate_polygons(&l.polygons)?;
            if let Some(z) = &l.z_mm {
                require(l.kind != "DRILL", "DRILL must not carry z_mm")?;
                require(
                    z.start_mm >= 0. && z.end_mm >= z.start_mm && !z.source.is_empty(),
                    "invalid absolute Z interval",
                )?;
            }
            if let Some(i) = l.copper_index {
                require(
                    l.copper() && l.context == "BOARD",
                    "copper_index requires BOARD copper layer",
                )?;
                require(copper.insert(i), "duplicate copper_index")?;
            }
            if let Some(t) = &l.thickness {
                t.validate()?;
            }
        }
        for l in &step.layers {
            if let Some([a, b]) = l.copper_span {
                require(a < b, "copper_span endpoints must increase")?;
                require(
                    copper.contains(&a) && copper.contains(&b),
                    format!(
                        "layer {:?}: copper_span endpoints must reference existing copper_index values",
                        l.name
                    ),
                )?;
            }
        }
        Ok(())
    }
}
impl Thickness {
    fn validate(&self) -> Result<()> {
        require(!self.source.is_empty(), "empty thickness source")?;
        let _ = (self.known, self.spec_deviation);
        require(
            self.assumed_density_g_cm3.is_none_or(|v| v > 0.),
            "invalid assumed copper density",
        )?;
        if let Some(kind) = self.kind.as_deref() {
            require(
                self.input_value.is_some(),
                "thickness provenance requires input_value",
            )?;
            require(
                match kind {
                    "areal_density_oz_ft2" => {
                        self.unit.as_deref() == Some("oz/ft2")
                            && self.thickness_source.as_deref() == Some("assumed_density")
                            && self.assumed_density_g_cm3.is_some()
                    }
                    "thickness_um" => self.unit.as_deref() == Some("um"),
                    "thickness" => matches!(self.unit.as_deref(), Some("mm" | "inch")),
                    _ => false,
                },
                "invalid thickness physical provenance",
            )?;
        }
        require(
            self.unit
                .as_deref()
                .is_none_or(|u| matches!(u, "mm" | "inch" | "um" | "oz/ft2")),
            "invalid thickness unit",
        )?;
        require(
            self.thickness_source
                .as_deref()
                .is_none_or(|u| matches!(u, "attribute" | "assumed_density")),
            "invalid thickness_source",
        )
    }
}
