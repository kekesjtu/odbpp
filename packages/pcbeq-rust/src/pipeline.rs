//! Complete native JSON -> MaterialMap path. No Python objects cross this boundary.
use crate::{
    document::{Board, Polygon},
    geos::{Geos, Result},
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
    let engine = Geos::open(Path::new(&options.geos_library))?;
    let step = &mut document.steps[0];
    for domain in &mut step.domains {
        normalize(&mut domain.polygons);
    }
    for layer in &mut step.layers {
        normalize(&mut layer.polygons);
    }
    let geometry_start = Instant::now();
    let geometry = planning::plan(&engine, step, &options)?;
    let geometry_end = Instant::now();
    let mesh = native_mesh::build(&engine, step, &geometry, &options)?;
    let mesh_end = Instant::now();
    let drills = native_sampling::drills(step)?;
    let mut plans = std::collections::HashMap::new();
    let publication = crate::native_publication::Publication::new(output)?;
    let mut writer = Writer::open(&publication.path, &step.name)?;
    let mut layer_reports = vec![];
    let mut sample_seconds = 0.;
    let mut write_seconds = 0.;
    let mut layers = 0;
    for layer in &step.layers {
        if !layer.physical() {
            layer_reports.push(json!({"name":layer.name,"type":layer.kind,"status":"skipped", "reason":if layer.context!="BOARD"{"non-BOARD context has no physical slab"}else{"only copper and dielectric layers are equivalented"}}));
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
        let plan = plans
            .entry(domain)
            .or_insert_with(|| native_sampling::sample_plan(&mesh, &geometry, domain));
        let pair = materials.resolve(
            &layer.name,
            if layer.copper() {
                "copper"
            } else {
                "dielectric"
            },
        )?;
        if layer.kind == "DIELECTRIC"
            && !layer.z_mm.as_ref().unwrap().thickness_known
            && !drills.is_empty()
        {
            if layer.copper_span.is_none() {
                materials.warnings.push(format!("equiv.drill_z_fallback: layer {:?} has unknown thickness and no copper_span; drill coverage uses best-effort Z", layer.name));
            } else {
                if drills.iter().any(|d| {
                    native_sampling::matched(layer, d) != native_sampling::matched_by_z(layer, d)
                }) {
                    materials.warnings.push(format!("equiv.drill_z_mismatch: layer {:?} copper_span differs from best-effort Z; copper-plane topology is used", layer.name));
                }
            }
        }
        let before = Instant::now();
        let (values, report) =
            native_sampling::evaluate(step, layer, &drills, &geometry, &mesh, domain, plan, pair)?;
        sample_seconds += before.elapsed().as_secs_f64();
        let before = Instant::now();
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
        "cells":writer.cells,"regions":writer.regions,"layers":layers,"raster":mesh.pixels,
        "cells_per_s":writer.cells as f64/elapsed,"materialmap_bytes":fs::metadata(&writer.destination).map_err(|e|e.to_string())?.len(),
        "warnings":materials.warnings,"numerical_backend":"rust","execution":"native-json-to-materialmap",
        "native_geometry_operations":engine.timings()});
    let grid_regions:Vec<_>=geometry.rectangles.iter().zip(&mesh.axes).enumerate().map(|(i,(r,(x,y)))|
        json!({"region_id":i,"bounds_mm":{"x":[r[0],r[2]],"y":[r[1],r[3]]},"grid_counts":{"x":x.len()-1,"y":y.len()-1,"z":1}})).collect();
    let audit = json!({"numerical_backend":"rust","execution":"native-json-to-materialmap","step":step.name,
        "drill_model":"solid_copper","drill_scope":"dielectric_only","cell_count":writer.cells,
        "raster":{"px":mesh.pixels[0],"py":mesh.pixels[1],"board_bounds_mm":geometry.bounds,
            "pixel_step_mm":[(geometry.bounds[2]-geometry.bounds[0])/mesh.pixels[0]as f64,(geometry.bounds[3]-geometry.bounds[1])/mesh.pixels[1]as f64]},
        "grid":{"layout":"region-local-cartesian","conformity_required":false,
            "region_count":geometry.rectangles.len(),"cell_count":mesh.cell_count,"regions":grid_regions,
            "mesh_configuration":mesh.config,"size_constraints_mm":{"max":mesh.config["max_cell_mm"]}},
        "rectification":geometry.report,"layers":layer_reports,"materials":{"defaults":crate::native_materials::defaults(),"by_layer":materials.by_layer},
        "diagnostics":materials.warnings.iter().map(|w|json!({"level":"warning","code":w.split(':').next().unwrap_or("warning"),"message":w,"path":null})).collect::<Vec<_>>(),
        "runtime":{"elapsed_s":elapsed},"performance":summary});
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
