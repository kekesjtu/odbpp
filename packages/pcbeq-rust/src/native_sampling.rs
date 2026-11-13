//! Material sampling on independent region rasters in physical coordinates.
use crate::{
    Result,
    diagnostics::Diagnostics,
    document::{Layer, Polygon, Step},
    native_mesh::Mesh,
    planning::Geometry,
    raster_index, sampling,
};
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
        let (start, end) = layer
            .start_layer
            .as_ref()
            .zip(layer.end_layer.as_ref())
            .ok_or(format!(
                "drill layer {:?}: missing start_layer/end_layer",
                layer.name
            ))?;
        let top = step
            .layers
            .iter()
            .find(|l| &l.name == start)
            .ok_or(format!(
                "drill layer {:?}: missing start copper layer",
                layer.name
            ))?;
        let bottom = step.layers.iter().find(|l| &l.name == end).ok_or(format!(
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
    diagnostics: &mut Diagnostics,
) -> Result<Vec<Vec<[f64; 5]>>> {
    let source = diagnostics.measure("sampling", || {
        let domain_doc = step
            .domains
            .iter()
            .find(|d| d.id == geometry.domain_ids[domain])
            .ok_or("missing sample domain")?;
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
        Ok(raster_index::Materials::new(
            edges(&domain_doc.polygons),
            &black,
        ))
    })?;
    let mut regions = vec![vec![]; geometry.rectangles.len()];
    for &rid in &geometry.members[domain] {
        let tile = &mesh.rasters[rid];
        // The renderer initializes domain-exterior pixels as the base phase.
        let pixels = diagnostics.measure("sampling", || source.render(tile.x, tile.y))?;
        regions[rid] = diagnostics.measure("equivalence", || {
            let values = sampling::integrate_pixels(
                &pixels,
                tile.x.2,
                tile.y.2,
                tile.windows.clone(),
                materials,
                mesh.minimum,
            )?;
            let mut checked = Vec::with_capacity(values.len());
            for (q, value) in values.into_iter().enumerate() {
                let mut properties = value.ok_or_else(|| format!(
                    "layer {:?} region {rid} cell {q}: insufficient raster samples or domain/geometry contradiction; increase px/py", layer.name))?;
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
                checked.push(properties);
            }
            Ok(checked)
        })?;
    }
    Ok(regions)
}
