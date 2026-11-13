//! Complete native JSON -> MaterialMap path. No Python objects cross this boundary.
use crate::{
    Result,
    document::{Board, Polygon},
    native_materials::Materials,
    native_mesh,
    native_output::Writer,
    native_sampling,
    options::Options,
    planning,
};
use serde_json::{Value, json};
use std::{fs, path::Path, time::Instant};
fn normalize(polygons: &mut [Polygon]) {
    for polygon in polygons {
        for point in &mut polygon.points {
            point.0 = (point.0 * 1e6).round_ties_even() / 1e6;
            point.1 = (point.1 * 1e6).round_ties_even() / 1e6;
        }
        polygon.points.dedup();
        while polygon.points.len() > 1 && polygon.points.first() == polygon.points.last() {
            polygon.points.pop();
        }
    }
}
pub fn run(source: &Path, output: &Path, options: Options) -> Result<Value> {
    options.validate()?;
    let source = source
        .canonicalize()
        .map_err(|e| format!("cannot read input: {e}"))?;
    for name in ["materialmap.json", "diagnostics/run.json"] {
        if output.join(name).canonicalize().ok().as_ref() == Some(&source) {
            return Err("input must not be overwritten by outputs".into());
        }
    }
    let start = Instant::now();
    let bytes = fs::read(&source).map_err(|e| e.to_string())?;
    let mut document: Board =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid intermediate JSON: {e}"))?;
    document.validate()?;
    let read_end = Instant::now();
    let mut config = options.materials.clone();
    if let Some(path) = &options.materials_path {
        let base: Value = serde_json::from_slice(
            &fs::read(path).map_err(|e| format!("cannot read materials: {e}"))?,
        )
        .map_err(|e| e.to_string())?;
        config = merge_config(base, config)?;
    }
    let mut materials = Materials::new(config)?;
    let step = &mut document.steps[0];
    for domain in &mut step.domains {
        normalize(&mut domain.polygons);
    }
    for layer in &mut step.layers {
        normalize(&mut layer.polygons);
    }
    let geometry_start = Instant::now();
    let mut geometry = planning::plan(step, &options)?;
    let geometry_end = Instant::now();
    let mesh = native_mesh::build(step, &geometry, &options)?;
    let mesh_end = Instant::now();
    let drills = native_sampling::drills(step)?;
    let publication = crate::native_publication::Publication::new(output)?;
    let mut writer = Writer::open(&publication.path, &step.name)?;
    let mut layer_reports = vec![];
    let mut region_indices = vec![None; geometry.rectangles.len()];
    let mut sample_seconds = 0.;
    let mut write_seconds = 0.;
    let mut layers = 0;
    for layer in &step.layers {
        if !layer.physical() {
            layer_reports.push(json!({"name":layer.name,"reason":if layer.context!="BOARD"{"non-BOARD context has no physical slab"}else{"only copper and dielectric layers are equivalented"}}));
            continue;
        }
        if layer.geometry_status != "ready" {
            return Err(format!(
                "layer {:?}: geometry is {}: {:?}",
                layer.name, layer.geometry_status, layer.geometry_error
            ));
        }
        if layer.z_mm.is_none() {
            return Err(format!("layer {:?}: no absolute Z interval", layer.name));
        }
        let id = layer
            .domain_id
            .as_ref()
            .ok_or(format!("layer {:?}: no computational domain", layer.name))?;
        let domain = geometry
            .domain_ids
            .iter()
            .position(|key| key == id)
            .ok_or("unplanned physical domain")?;
        let pair = materials.resolve(
            &layer.name,
            if layer.copper() {
                "copper"
            } else {
                "dielectric"
            },
        )?;
        let before = Instant::now();
        let (values, report) =
            native_sampling::evaluate(step, layer, &drills, &geometry, &mesh, domain, pair)?;
        sample_seconds += before.elapsed().as_secs_f64();
        let before = Instant::now();
        for (offset, &rid) in geometry.members[domain].iter().enumerate() {
            region_indices[rid].get_or_insert(writer.regions + offset);
        }
        writer.layer(layer, &geometry, &mesh, domain, &values)?;
        write_seconds += before.elapsed().as_secs_f64();
        layer_reports.push(report);
        layers += 1;
    }
    let before = Instant::now();
    writer.finish()?;
    write_seconds += before.elapsed().as_secs_f64();
    let end = Instant::now();
    let elapsed = end.duration_since(start).as_secs_f64();
    let mut summary = json!({"durations_s":{"read_json":read_end.duration_since(start).as_secs_f64(),
        "equivalence_and_write":end.duration_since(read_end).as_secs_f64(),"total":elapsed,
        "geometry":geometry_end.duration_since(geometry_start).as_secs_f64(),
        "mesh":mesh_end.duration_since(geometry_end).as_secs_f64(),"sampling":sample_seconds,"write":write_seconds},
        "cells":writer.cells,"regions":writer.regions,"layers":layers,
        "cells_per_s":writer.cells as f64/elapsed,"materialmap_bytes":fs::metadata(&writer.destination).map_err(|e|e.to_string())?.len(),
        "warnings":materials.warnings});
    let region_indices = region_indices
        .into_iter()
        .map(|index| index.ok_or_else(|| "unpublished geometry region".to_string()))
        .collect::<Result<Vec<_>>>()?;
    for profile in geometry.report["profiles"]
        .as_object_mut()
        .unwrap()
        .values_mut()
    {
        for index in profile["regions"].as_array_mut().unwrap() {
            *index = json!(region_indices[index.as_u64().unwrap() as usize]);
        }
    }
    let mut raster = mesh.raster_report;
    for tile in raster["regions"].as_array_mut().unwrap() {
        tile["region"] = json!(region_indices[tile["region"].as_u64().unwrap() as usize]);
    }
    let audit = json!({"raster":raster,"grid":mesh.config,"rectification":geometry.report,
        "layers":layer_reports,"materials":materials.by_layer,
        "warnings":materials.warnings.iter().map(|w|json!({"code":w.split(':').next().unwrap_or("warning"),"message":w})).collect::<Vec<_>>(),
        "durations_s":summary["durations_s"]});
    let diagnostics = publication.path.join("diagnostics");
    fs::create_dir_all(&diagnostics).map_err(|e| e.to_string())?;
    let temporary = diagnostics.join(format!(".run-{}.tmp", std::process::id()));
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&audit).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    crate::native_output::replace(&temporary, &diagnostics.join("run.json"))?;
    let publish_start = Instant::now();
    publication.commit()?;
    let publish_seconds = publish_start.elapsed().as_secs_f64();
    for name in ["total", "equivalence_and_write", "write"] {
        let current = summary["durations_s"][name].as_f64().unwrap();
        summary["durations_s"][name] = json!(current + publish_seconds);
    }
    summary["durations_s"]["publication"] = json!(publish_seconds);
    summary["cells_per_s"] = json!(writer.cells as f64 / (elapsed + publish_seconds));
    Ok(summary)
}
fn merge_config(mut base: Value, overrides: Value) -> Result<Value> {
    let object = base
        .as_object_mut()
        .ok_or("materials file must contain a JSON object")?;
    for (section, value) in overrides
        .as_object()
        .ok_or("material overrides must be an object")?
    {
        let entry = object.entry(section.clone()).or_insert_with(|| json!({}));
        let target = entry
            .as_object_mut()
            .ok_or("material section must be an object")?;
        for (key, value) in value
            .as_object()
            .ok_or("material override section must be an object")?
        {
            let phase = target.entry(key.clone()).or_insert_with(|| json!({}));
            phase
                .as_object_mut()
                .ok_or("material phase must be an object")?
                .extend(
                    value
                        .as_object()
                        .ok_or("material phase override must be an object")?
                        .clone(),
                );
        }
    }
    Ok(base)
}
