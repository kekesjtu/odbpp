//! Independent effective profiles; geometrically equal inputs share one plan.
use crate::{
    document::{self, Step},
    fast,
    geos::{Geos, Result},
    options::Options,
    rectangles::Rect,
};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub struct Geometry {
    pub rectangles: Vec<Rect>,
    pub domain_ids: Vec<String>,
    pub members: Vec<Vec<usize>>,
    pub report: Value,
    pub bounds: Rect,
}

pub fn plan(engine: &Geos, step: &Step, options: &Options) -> Result<Geometry> {
    let profile = step
        .profile_domain_id
        .as_ref()
        .ok_or("missing step profile")?;
    let board = step
        .domains
        .iter()
        .find(|d| &d.id == profile)
        .ok_or("missing step profile")?;
    let board = document::shape(engine, &board.polygons)?;
    let bounds = board.bounds()?;
    let mut ids: Vec<_> = step
        .layers
        .iter()
        .filter(|l| l.physical())
        .filter_map(|l| l.domain_id.clone())
        .collect();
    ids.sort();
    ids.dedup();
    if ids.is_empty() {
        return Err("no physical material layers".into());
    }
    let mut originals = vec![];
    let mut plans: Vec<(Vec<usize>, Value)> = vec![];
    let mut rectangles = vec![];
    let mut members = vec![];
    let mut reports = BTreeMap::new();
    let mut reused = 0;
    for id in &ids {
        let authored = step
            .domains
            .iter()
            .find(|d| &d.id == id)
            .ok_or("missing physical domain")?;
        let original = document::shape(engine, &authored.polygons)?.intersection(&board)?;
        if original.empty()? || original.area()? <= 0. {
            return Err(format!(
                "domain {id:?} has no physical area within the step profile"
            ));
        }
        let mut existing = None;
        for (i, prior) in originals.iter().enumerate() {
            if original.equals(prior)? {
                existing = Some(i);
                break;
            }
        }
        let (region_ids, mut report) = if let Some(i) = existing {
            reused += 1;
            let mut report = plans[i].1.clone();
            report["reused_from"] = report["canonical_domain_id"].clone();
            (plans[i].0.clone(), report)
        } else {
            let plan = fast::approximate(&original, options.max_area_error_ratio)
                .map_err(|e| format!("domain {id:?}: {e}"))?;
            let region_ids: Vec<_> =
                (rectangles.len()..rectangles.len() + plan.rectangles.len()).collect();
            rectangles.extend(plan.rectangles);
            let mut report = plan.audit;
            report["canonical_domain_id"] = json!(id);
            originals.push(original);
            plans.push((region_ids.clone(), report.clone()));
            (region_ids, report)
        };
        report["region_ids"] = json!(region_ids);
        members.push(region_ids);
        reports.insert(id.clone(), report);
    }
    let domain_regions: BTreeMap<_, _> = ids.iter().zip(&members).collect();
    let report = json!({"ok":true,"strategy":"fast-greedy-independent-profiles",
        "rectangle_count":rectangles.len(),"profile_count":ids.len(),"unique_profile_count":plans.len(),
        "reused_profile_count":reused,"relative_area_budget":options.max_area_error_ratio,
        "rectangles":rectangles.iter().map(|r|json!({"x0_mm":r[0],"y0_mm":r[1],"x1_mm":r[2],"y1_mm":r[3]})).collect::<Vec<_>>(),
        "audit":{"domains":reports,"domain_region_ids":domain_regions,
            "relative_area_budget":options.max_area_error_ratio,
            "cross_layer_alignment_required":false,"per_profile_non_overlapping":true,
            "effective_profile_identity":"GEOS topological equality after board intersection"}});
    Ok(Geometry {
        rectangles,
        domain_ids: ids,
        members,
        report,
        bounds,
    })
}
