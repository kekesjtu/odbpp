//! Complete native JSON -> MaterialMap path. No Python objects cross this boundary.
use crate::{
    Result,
    diagnostics::Diagnostics,
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
    // Refuse destructive output aliases before attempting even a failure report.
    for input in [
        Some(source),
        options.materials_path.as_deref().map(Path::new),
    ]
    .into_iter()
    .flatten()
    {
        if let Ok(input) = input.canonicalize() {
            for name in crate::native_publication::OUTPUTS {
                if let Ok(target) = output.join(name).canonicalize()
                    && (input == target || input.starts_with(&target))
                {
                    return Err("input must not be overwritten by outputs".into());
                }
            }
        }
    }
    let mut diagnostics = Diagnostics::new();
    let result = execute(source, output, options, &mut diagnostics);
    if let Err(error) = &result {
        diagnostics.finish(Some(error));
        if let Err(write_error) = diagnostics.write(output) {
            return Err(format!(
                "{error}; cannot publish diagnostics: {write_error}"
            ));
        }
    }
    result
}

fn execute(
    source: &Path,
    output: &Path,
    options: Options,
    diagnostics: &mut Diagnostics,
) -> Result<Value> {
    let mut document: Board = diagnostics.measure("read_validate", || {
        options.validate()?;
        let bytes = fs::read(source).map_err(|e| format!("cannot read input: {e}"))?;
        let document: Board = serde_json::from_slice(&bytes)
            .map_err(|e| format!("invalid intermediate JSON: {e}"))?;
        document.validate()?;
        Ok(document)
    })?;
    let mut materials = diagnostics.measure("prepare", || {
        let mut config = options.materials.clone();
        if let Some(path) = &options.materials_path {
            let base = serde_json::from_slice(
                &fs::read(path).map_err(|e| format!("cannot read materials: {e}"))?,
            )
            .map_err(|e| e.to_string())?;
            config = merge_config(base, config)?;
        }
        let materials = Materials::new(config)?;
        for profile in &mut document.steps[0].profiles {
            normalize(&mut profile.polygons);
        }
        for layer in &mut document.steps[0].layers {
            normalize(&mut layer.polygons);
        }
        Ok(materials)
    })?;
    let step = &document.steps[0];
    diagnostics.measure("prepare", || {
        for layer in step.layers.iter().filter(|l| l.physical()) {
            if layer.geometry_status != "ready" {
                return Err(format!(
                    "layer {:?}: geometry is {}: {:?}",
                    layer.name, layer.geometry_status, layer.geometry_error
                ));
            }
            if layer.z_mm.is_none() {
                return Err(format!("layer {:?}: no absolute Z interval", layer.name));
            }
            if step.profile_for_layer(&layer.name).is_none() {
                return Err(format!("layer {:?}: no computational profile", layer.name));
            }
        }
        Ok(())
    })?;
    for layer in step.layers.iter().filter(|l| l.physical()) {
        let z = layer.z_mm.as_ref().unwrap();
        // The layer's own thickness and the cumulative position above it are
        // separate facts; a null says which one is missing.
        let (code, message) = if z.thickness_mm.is_none() {
            ("z.thickness_unknown", "layer thickness is not stated")
        } else if z.start_mm.is_none() {
            (
                "z.absolute_position_unknown",
                "absolute Z interval is uncertain",
            )
        } else {
            continue;
        };
        diagnostics.value["issues"]["errors"]
            .as_array_mut()
            .unwrap()
            .push(json!({"code":code,"message":message,"layers":[layer.name],"blocking":false}));
    }
    let geometry = planning::plan(step, &options, diagnostics)?;
    diagnostics.geometry(step, &geometry);
    let mesh = diagnostics.measure("mesh", || native_mesh::build(step, &geometry, &options))?;
    diagnostics.mesh(&geometry, &mesh);
    let drills = diagnostics.measure("prepare", || native_sampling::drills(step))?;
    let publication = diagnostics.measure("write", || {
        crate::native_publication::Publication::new(output)
    })?;
    let mut writer =
        diagnostics.measure("write", || Writer::open(&publication.path, &step.name))?;
    let mut layers = 0;
    for layer in step.layers.iter().filter(|l| l.physical()) {
        let profile = geometry
            .profile_ids
            .iter()
            .position(|key| Some(key) == step.profile_for_layer(&layer.name).map(|p| &p.id))
            .ok_or("unplanned physical profile")?;
        let pair = diagnostics.measure("prepare", || {
            materials.resolve(
                &layer.name,
                if layer.copper() {
                    "copper"
                } else {
                    "dielectric"
                },
            )
        })?;
        diagnostics.value["issues"]["warnings"] = json!(materials.warnings);
        let values = native_sampling::evaluate(
            step,
            layer,
            &drills,
            &geometry,
            &mesh,
            profile,
            pair,
            diagnostics,
        )?;
        diagnostics.measure("write", || {
            writer.layer(layer, &geometry, &mesh, profile, &values)
        })?;
        layers += 1;
    }
    diagnostics.measure("write", || writer.finish())?;
    let bytes = fs::metadata(&writer.destination)
        .map_err(|e| e.to_string())?
        .len();
    diagnostics.stage = "publication";
    let publish_start = Instant::now();
    let published = publication.commit(|| {
        diagnostics.add_time("publication", publish_start.elapsed().as_secs_f64());
        diagnostics.finish(None);
        diagnostics.write(&publication.path)
    });
    if published.is_err() {
        // Include a failed publication and any rollback in the failure timing.
        diagnostics.value["performance"]["stages_s"]["publication"] =
            json!(publish_start.elapsed().as_secs_f64());
    }
    published?;
    let total = diagnostics.value["performance"]["total_s"]
        .as_f64()
        .unwrap();
    Ok(
        json!({"performance":diagnostics.value["performance"],"issues":diagnostics.value["issues"],
        "cells":writer.cells,"regions":writer.regions,"layers":layers,
        "cells_per_s":writer.cells as f64/total,"materialmap_bytes":bytes}),
    )
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
