//! Independent effective profiles; geometrically equal inputs share one plan.
use crate::{
    Result,
    diagnostics::Diagnostics,
    document::{self, Step},
    fast,
    options::Options,
    rectangles::Rect,
};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub struct Geometry {
    pub rectangles: Vec<Rect>,
    pub profile_ids: Vec<String>,
    pub members: Vec<Vec<usize>>,
    pub report: Value,
    pub bounds: Rect,
}

pub fn plan(step: &Step, options: &Options, diagnostics: &mut Diagnostics) -> Result<Geometry> {
    let (bounds, ids) = diagnostics.measure("profiles", || {
        let profile = step.profile_id.as_ref().ok_or("missing step profile")?;
        let board = step
            .profiles
            .iter()
            .find(|d| &d.id == profile)
            .ok_or("missing step profile")?;
        let board = document::shape(&board.polygons)?;
        let bounds = board.bounds()?;
        let mut ids: Vec<_> = step
            .layers
            .iter()
            .filter(|l| l.physical())
            .filter_map(|l| step.profile_for_layer(&l.name).map(|p| p.id.clone()))
            .collect();
        ids.sort();
        ids.dedup();
        if ids.is_empty() {
            return Err("no physical material layers".into());
        }
        Ok((bounds, ids))
    })?;
    let mut originals = vec![];
    let mut plans: Vec<(Vec<usize>, String)> = vec![];
    let mut rectangles = vec![];
    let mut members = vec![];
    let mut reports = BTreeMap::new();
    let mut profile_aliases = BTreeMap::new();
    for id in &ids {
        let (original, existing) = diagnostics.measure("profiles", || {
            let authored = step
                .profiles
                .iter()
                .find(|d| &d.id == id)
                .ok_or("missing physical profile")?;
            let original = document::shape(&authored.polygons)?;
            if original.empty() || original.area() <= 0. {
                return Err(format!("profile {id:?} has no physical area"));
            }
            let mut existing = None;
            for (i, prior) in originals.iter().enumerate() {
                if original.equals(prior) {
                    existing = Some(i);
                    break;
                }
            }
            Ok((original, existing))
        })?;
        let (region_ids, canonical) = if let Some(i) = existing {
            plans[i].clone()
        } else {
            let plan = diagnostics.measure("rectification", || {
                fast::approximate(&original, options.max_area_error_ratio)
                    .map_err(|e| format!("profile {id:?}: {e}"))
            })?;
            let region_ids: Vec<_> =
                (rectangles.len()..rectangles.len() + plan.rectangles.len()).collect();
            rectangles.extend(plan.rectangles);
            let mut report = plan.audit;
            report.as_object_mut().unwrap().remove("ok");
            report["regions"] = json!(region_ids);
            originals.push(original);
            plans.push((region_ids.clone(), id.clone()));
            reports.insert(id.clone(), report);
            (region_ids, id.clone())
        };
        profile_aliases.insert(id.clone(), canonical);
        members.push(region_ids);
    }
    let report = json!({"max_area_error_ratio":options.max_area_error_ratio,
        "profile_aliases":profile_aliases,"profiles":reports});
    Ok(Geometry {
        rectangles,
        profile_ids: ids,
        members,
        report,
        bounds,
    })
}
