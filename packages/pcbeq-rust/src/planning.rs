use crate::{
    coarsen::{Rect, area, unique},
    document::{self, Step},
    geos::{Geos, Result, Shape},
    options::Options,
    rectification::{self, Plan},
    shared::{self, Tagged},
    topology::Source,
};
use serde_json::{Value, json};
pub struct Geometry {
    pub rectangles: Vec<Rect>,
    pub domain_ids: Vec<String>,
    pub members: Vec<Vec<usize>>,
    pub report: Value,
    pub bounds: Rect,
}
fn check(original: &Shape<'_>, model: &Shape<'_>, options: &Options) -> Result<(bool, Value)> {
    let source = Source::new(original)?;
    let topology = source.topology(model)?;
    let lost = original.difference(model)?.area()?;
    let gained = model.difference(original)?.area()?;
    let ok = topology["preserved"] == true
        && lost <= original.area()? * options.max_area_loss_ratio + 1e-8
        && gained <= 1e-8;
    Ok((
        ok,
        json!({"ok":ok,"shared_model_area_mm2":model.area()?,"shared_loss_area_mm2":lost,
        "shared_topology":topology}),
    ))
}
pub fn plan(engine: &Geos, step: &Step, options: &Options) -> Result<Geometry> {
    let mut attempts = vec![];
    let mut last = String::new();
    for divisor in [1., 2., 4., 8., 16.] {
        let cap = options.max_area_loss_ratio / divisor;
        match once(engine, step, options, cap) {
            Ok(mut geometry) => {
                attempts.push(json!({"planning_area_loss_ratio":cap,"ok":true}));
                geometry.report["audit"]["shared_domain_replanning"] = json!(attempts);
                return Ok(geometry);
            }
            Err(error) if error.starts_with("shared domain") => {
                attempts.push(json!({"planning_area_loss_ratio":cap,"ok":false,"error":error}));
                last = error;
            }
            Err(error) => return Err(error),
        }
    }
    Err(format!("{last}; shared_domain_replanning={attempts:?}"))
}
fn once(engine: &Geos, step: &Step, options: &Options, cap: f64) -> Result<Geometry> {
    let profile = step
        .profile_domain_id
        .as_ref()
        .ok_or("missing step profile")?;
    let profile_domain = step
        .domains
        .iter()
        .find(|d| &d.id == profile)
        .ok_or("missing step profile")?;
    let original = document::shape(engine, &profile_domain.polygons)?;
    let bounds = original.bounds()?;
    let base = rectification::rectify(&original, options, bounds, cap, &[vec![], vec![]])?;
    let refs = [
        unique(base.rectangles.iter().flat_map(|r| [r[0], r[2]])),
        unique(base.rectangles.iter().flat_map(|r| [r[1], r[3]])),
    ];
    let mut ids = step
        .layers
        .iter()
        .filter(|l| l.physical())
        .filter_map(|l| l.domain_id.clone())
        .collect::<Vec<_>>();
    ids.sort();
    ids.dedup();
    ids.retain(|id| id != profile);
    ids.insert(0, profile.clone());
    let physical: Vec<_> = ids
        .iter()
        .enumerate()
        .filter(|(_, id)| {
            step.layers
                .iter()
                .any(|l| l.physical() && l.domain_id.as_ref() == Some(id))
        })
        .map(|(i, _)| i)
        .collect();
    if physical.is_empty() {
        return Err("no physical material layers".into());
    }
    let mut originals = vec![original];
    let mut plans = vec![base];
    for id in ids.iter().skip(1) {
        let domain = step
            .domains
            .iter()
            .find(|d| &d.id == id)
            .ok_or("missing layer domain")?;
        let authored = document::shape(engine, &domain.polygons)?;
        let original = authored.intersection(&originals[0])?;
        if original.empty()? || original.area()? <= 0. {
            return Err(format!(
                "domain {id:?} has no physical area within the step profile"
            ));
        }
        let mut duplicate = None;
        for (i, prior) in originals.iter().enumerate() {
            if original.equals(prior)? {
                duplicate = Some(i);
                break;
            }
        }
        let plan = if let Some(i) = duplicate {
            Plan {
                rectangles: plans[i].rectangles.clone(),
                audit: plans[i].audit.clone(),
                ok: plans[i].ok,
            }
        } else {
            rectification::rectify(&original, options, bounds, cap, &refs)?
        };
        originals.push(original);
        plans.push(plan);
    }
    let step_model = engine.rectangles(&plans[0].rectangles)?;
    let models = plans
        .iter()
        .map(|p| engine.rectangles(&p.rectangles)?.intersection(&step_model))
        .collect::<Result<Vec<_>>>()?;
    let mut audit = serde_json::Map::new();
    for (i, (original, model)) in originals.iter().zip(&models).enumerate() {
        let (ok, checked) = check(original, model, options)?;
        if !ok {
            return Err(format!("shared domain {:?} failed: {checked}", ids[i]));
        }
        let mut record = plans[i].audit.clone();
        record
            .as_object_mut()
            .unwrap()
            .extend(checked.as_object().unwrap().clone());
        audit.insert(ids[i].clone(), record);
    }
    let mut rectangles = plans[0].rectangles.clone();
    for (i, plan) in plans.iter().enumerate().skip(1) {
        if models[i].equals(&step_model)? {
            continue;
        }
        for cut in &plan.rectangles {
            let mut pieces = vec![];
            for r in rectangles {
                let (xa, ya, xb, yb) = (
                    r[0].max(cut[0]),
                    r[1].max(cut[1]),
                    r[2].min(cut[2]),
                    r[3].min(cut[3]),
                );
                if xb - xa <= 1e-9 || yb - ya <= 1e-9 {
                    pieces.push(r);
                    continue;
                }
                pieces.push([xa, ya, xb, yb]);
                if xa > r[0] + 1e-9 {
                    pieces.push([r[0], r[1], xa, r[3]]);
                }
                if xb < r[2] - 1e-9 {
                    pieces.push([xb, r[1], r[2], r[3]]);
                }
                if ya > r[1] + 1e-9 {
                    pieces.push([xa, r[1], xb, ya]);
                }
                if yb < r[3] - 1e-9 {
                    pieces.push([xa, yb, xb, r[3]]);
                }
            }
            rectangles = pieces;
        }
    }
    let mut tagged = vec![];
    for rect in rectangles {
        let shape = engine.rectangle(rect)?;
        let mut owners = vec![];
        for (i, model) in models.iter().enumerate() {
            if model.covers(&shape)? {
                owners.push(i);
            } else if model.intersection(&shape)?.area()? > 1e-8 {
                return Err(format!(
                    "domain {:?} partially covers region {rect:?}",
                    ids[i]
                ));
            }
        }
        tagged.push(Tagged { rect, owners });
    }
    let initial = tagged.len();
    tagged = shared::exact_merge(tagged, &physical);
    let supported = engine.union(
        &physical
            .iter()
            .map(|&i| originals[i].copy())
            .collect::<Result<Vec<_>>>()?,
    )?;
    let mut shared_originals = originals
        .iter()
        .map(Shape::copy)
        .collect::<Result<Vec<_>>>()?;
    shared_originals[0] = supported.copy()?;
    let (tagged, merging) = shared::merge(tagged, &shared_originals, options.max_area_loss_ratio)?;
    let tagged = shared::exact_merge(tagged, &physical);
    let rectangles: Vec<_> = tagged.iter().map(|r| r.rect).collect();
    let members: Vec<Vec<usize>> = (0..ids.len())
        .map(|i| {
            tagged
                .iter()
                .enumerate()
                .filter(|(_, r)| r.owners.contains(&i))
                .map(|(n, _)| n)
                .collect()
        })
        .collect();
    let mut final_audits = serde_json::Map::new();
    for i in 0..ids.len() {
        let model = engine.rectangles(
            &members[i]
                .iter()
                .map(|&n| rectangles[n])
                .collect::<Vec<_>>(),
        )?;
        let (ok, record) = check(&shared_originals[i], &model, options)?;
        if !ok {
            return Err(format!(
                "shared domain {:?} final validation failed: {record}",
                ids[i]
            ));
        }
        let mut base = audit[&ids[i]].clone();
        base.as_object_mut()
            .unwrap()
            .extend(record.as_object().unwrap().clone());
        final_audits.insert(ids[i].clone(), base);
    }
    let active = engine.rectangles(&rectangles)?;
    if (rectangles.iter().copied().map(area).sum::<f64>() - active.area()?).abs() > 1e-7 {
        return Err("shared region partition overlaps".into());
    }
    let lost = supported.difference(&active)?.area()?;
    let outside = active.difference(&supported)?.area()?;
    let source = Source::new(&supported)?;
    let mut report = plans[0].audit.clone();
    report["rectangles"] = json!(
        rectangles
            .iter()
            .map(|r| json!({"x0_mm":r[0],"y0_mm":r[1],"x1_mm":r[2],"y1_mm":r[3]}))
            .collect::<Vec<_>>()
    );
    report["original_area_mm2"] = json!(supported.area()?);
    report["rectified_area_mm2"] = json!(active.area()?);
    report["lost_area_mm2"] = json!(lost);
    report["gained_area_mm2"] = json!(outside);
    report["relative_area_error"] = json!(lost / supported.area()?);
    report["net_area_error_mm2"] = json!(active.area()? - supported.area()?);
    report["shape_deviation_mm2"] = json!(lost + outside);
    report["rectangle_count"] = json!(rectangles.len());
    report["audit"]["domains"] = json!(final_audits);
    report["audit"]["domain_region_ids"] = json!(
        ids.iter()
            .cloned()
            .zip(members.iter().cloned())
            .collect::<std::collections::BTreeMap<_, _>>()
    );
    report["audit"]["active_physical_domain_ids"] =
        json!(physical.iter().map(|&i| &ids[i]).collect::<Vec<_>>());
    report["audit"]["domains"][profile]["shared_membership_source"] =
        json!("supported_physical_domain_union");
    report["audit"]["shared_interval_merging"] = merging;
    report["audit"]["partition_non_overlapping"] = json!(true);
    report["audit"]["non_overlapping"] = json!(true);
    report["audit"]["area_budget_mm2"] = json!(supported.area()? * options.max_area_loss_ratio);
    report["audit"]["reference_step_model_area_mm2"] = json!(step_model.area()?);
    report["audit"]["active_partition_cleanup"] =
        json!({"before":initial,"after":rectangles.len()});
    report["audit"]["topology"] = source.topology(&active)?;
    report["ok"] = json!(true);
    Ok(Geometry {
        rectangles,
        domain_ids: ids,
        members,
        report,
        bounds,
    })
}
