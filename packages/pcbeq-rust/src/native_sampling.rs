//! Material sampling on independent region rasters in physical coordinates.
use crate::{
    Result,
    document::{Layer, Polygon, Step},
    native_mesh::Mesh,
    planning::Geometry,
    raster_index, sampling,
};
use serde_json::{Value, json};
pub fn edges(polygons: &[Polygon]) -> Vec<(f64, f64, f64, f64)> {
    polygons
        .iter()
        .flat_map(|p| {
            p.points
                .iter()
                .zip(p.points.iter().cycle().skip(1))
                .take(p.points.len())
                .map(|(&(x, y), &(a, b))| (x, y, a, b))
        })
        .collect()
}
pub struct Drill<'a> {
    layer: &'a Layer,
    endpoint_rows: [u64; 2],
}
pub fn drills(step: &Step) -> Result<Vec<Drill<'_>>> {
    if !step
        .layers
        .iter()
        .any(|l| l.physical() && l.kind == "DIELECTRIC" && l.geometry_status == "ready")
    {
        return Ok(vec![]);
    }
    let mut drills = vec![];
    for layer in step
        .layers
        .iter()
        .filter(|l| l.context == "BOARD" && l.kind == "DRILL")
    {
        let span = layer.copper_span.ok_or(format!(
            "drill layer {:?}: cannot resolve copper_span",
            layer.name
        ))?;
        let top = step
            .layers
            .iter()
            .find(|l| l.copper_index == Some(span[0]))
            .ok_or(format!(
                "drill layer {:?}: missing start copper layer",
                layer.name
            ))?;
        let bottom = step
            .layers
            .iter()
            .find(|l| l.copper_index == Some(span[1]))
            .ok_or(format!(
                "drill layer {:?}: missing end copper layer",
                layer.name
            ))?;
        if layer.geometry_status != "ready" {
            return Err(format!(
                "drill layer {:?}: geometry is {}",
                layer.name, layer.geometry_status
            ));
        }
        drills.push(Drill {
            layer,
            endpoint_rows: [top.row, bottom.row],
        });
    }
    Ok(drills)
}
fn matched(layer: &Layer, drill: &Drill<'_>) -> bool {
    drill.endpoint_rows[0] < layer.row && layer.row < drill.endpoint_rows[1]
}
pub fn evaluate(
    step: &Step,
    layer: &Layer,
    drills: &[Drill<'_>],
    geometry: &Geometry,
    mesh: &Mesh,
    domain: usize,
    materials: [f64; 6],
) -> Result<(Vec<Vec<[f64; 5]>>, Value)> {
    let domain_doc = step
        .domains
        .iter()
        .find(|d| d.id == geometry.domain_ids[domain])
        .ok_or("missing sample domain")?;
    let profile = step
        .domains
        .iter()
        .find(|d| Some(&d.id) == step.profile_domain_id.as_ref())
        .ok_or("missing step domain")?;
    let covering: Vec<_> = if layer.kind == "DIELECTRIC" {
        drills.iter().filter(|d| matched(layer, d)).collect()
    } else {
        vec![]
    };
    let black: Vec<&[Polygon]> = if layer.copper() {
        vec![&layer.polygons]
    } else {
        covering
            .iter()
            .map(|d| d.layer.polygons.as_slice())
            .collect()
    };
    let source = raster_index::Materials::new(
        edges(&domain_doc.polygons),
        edges(&profile.polygons),
        &black,
    );
    let mut regions = vec![vec![]; geometry.rectangles.len()];
    let mut cells = 0;
    let mut base_pixels = 0;
    for &rid in &geometry.members[domain] {
        let tile = &mesh.rasters[rid];
        // The renderer initializes domain-exterior pixels as the base phase.
        let (pixels, outside) = source.render(tile.x, tile.y)?;
        base_pixels += outside;
        let values = sampling::integrate_pixels(
            &pixels,
            tile.x.2,
            tile.y.2,
            tile.windows.clone(),
            materials,
            mesh.minimum,
        )?;
        cells += values.len();
        for (q, value) in values.into_iter().enumerate() {
            let value = value.ok_or(format!("layer {:?} region {rid} cell {q}: insufficient raster samples or domain/geometry contradiction; increase px/py",layer.name))?;
            let mut properties = value;
            for (i, v) in properties.iter_mut().enumerate() {
                let factor = if i < 3 { 1e6 } else { 1e4 };
                let rounded = (*v * factor).round_ties_even() / factor;
                if rounded > 0. {
                    *v = rounded;
                }
                if !v.is_finite() || *v <= 0. {
                    return Err("non-finite/nonpositive material property".into());
                }
            }
            regions[rid].push(properties);
        }
    }
    let z = layer.z_mm.as_ref().ok_or("missing layer Z")?;
    let report = json!({"name":layer.name,"type":layer.kind,"status":"equivalent","domain_id":layer.domain_id,
        "domain_source":domain_doc.source,"cell_count":cells,
        "outside_material":"base","outside_base_pixels":base_pixels,
        "z_mm":{"start_mm":z.start_mm,"end_mm":z.end_mm,"thickness_known":z.thickness_known,"source":z.source},
        "drill_overlays":covering.iter().map(|d|&d.layer.name).collect::<Vec<_>>()});
    Ok((regions, report))
}
