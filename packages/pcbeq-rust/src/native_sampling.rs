//! Region-local physical windows, grouped by matching Y bands for prefix reuse.
use crate::{
    document::{Layer, Polygon, Step},
    geos::Result,
    native_mesh::Mesh,
    planning::Geometry,
    raster::{self, Axis},
    sampling,
};
use serde_json::{Value, json};
pub type Window = (usize, usize, usize, usize);
pub struct Samples {
    pub windows: Vec<Window>,
    pub locations: Vec<(usize, usize, usize)>,
}
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
fn pixel_range(lo: f64, hi: f64, axis: Axis) -> (usize, usize) {
    (raster::pixel_index(lo, axis), raster::pixel_index(hi, axis))
}
pub fn sample_plan(mesh: &Mesh, geometry: &Geometry, domain: usize) -> Samples {
    let r = geometry.bounds;
    let x = (r[0], (r[2] - r[0]) / mesh.pixels[0] as f64, mesh.pixels[0]);
    let y = (r[1], (r[3] - r[1]) / mesh.pixels[1] as f64, mesh.pixels[1]);
    let mut bands: Vec<((f64, f64), Vec<(Window, (usize, usize, usize))>)> = vec![];
    for &rid in &geometry.members[domain] {
        let (xs, ys) = &mesh.axes[rid];
        for (j, pair) in ys.windows(2).enumerate() {
            let (b0, b1) = pixel_range(pair[0], pair[1], y);
            let band = if let Some(i) = bands.iter().position(|b| b.0 == (pair[0], pair[1])) {
                i
            } else {
                bands.push(((pair[0], pair[1]), vec![]));
                bands.len() - 1
            };
            for (i, pair) in xs.windows(2).enumerate() {
                let (a0, a1) = pixel_range(pair[0], pair[1], x);
                bands[band].1.push(((a0, a1, b0, b1), (rid, i, j)));
            }
        }
    }
    let pairs: Vec<_> = bands.into_iter().flat_map(|b| b.1).collect();
    Samples {
        windows: pairs.iter().map(|p| p.0).collect(),
        locations: pairs.into_iter().map(|p| p.1).collect(),
    }
}
pub struct Drill<'a> {
    pub layer: &'a Layer,
    pub span: [usize; 2],
    pub z: [f64; 2],
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
            .and_then(|l| l.z_mm.as_ref())
            .ok_or(format!(
                "drill layer {:?}: endpoint copper layer missing z_mm",
                layer.name
            ))?;
        let bottom = step
            .layers
            .iter()
            .find(|l| l.copper_index == Some(span[1]))
            .and_then(|l| l.z_mm.as_ref())
            .ok_or(format!(
                "drill layer {:?}: endpoint copper layer missing z_mm",
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
            span,
            z: [top.start_mm, bottom.end_mm],
        });
    }
    Ok(drills)
}
pub fn matched(layer: &Layer, drill: &Drill<'_>) -> bool {
    if let Some([a, b]) = layer.copper_span {
        return a.max(drill.span[0]) < b.min(drill.span[1]);
    }
    matched_by_z(layer, drill)
}
pub fn matched_by_z(layer: &Layer, drill: &Drill<'_>) -> bool {
    let Some(z) = &layer.z_mm else {
        return false;
    };
    if z.end_mm - z.start_mm <= 1e-9 {
        drill.z[0] - 1e-9 <= z.start_mm && z.start_mm <= drill.z[1] + 1e-9
    } else {
        drill.z[0] < z.end_mm - 1e-9 && z.start_mm < drill.z[1] - 1e-9
    }
}
pub fn evaluate(
    step: &Step,
    layer: &Layer,
    drills: &[Drill<'_>],
    geometry: &Geometry,
    mesh: &Mesh,
    domain: usize,
    plan: &Samples,
    materials: [f64; 6],
) -> Result<(Vec<Vec<[f64; 5]>>, Value)> {
    let r = geometry.bounds;
    let x = (r[0], (r[2] - r[0]) / mesh.pixels[0] as f64, mesh.pixels[0]);
    let y = (r[1], (r[3] - r[1]) / mesh.pixels[1] as f64, mesh.pixels[1]);
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
    let black = if layer.copper() {
        vec![edges(&layer.polygons)]
    } else {
        covering.iter().map(|d| edges(&d.layer.polygons)).collect()
    };
    let pixels = raster::rasterize_pixels(
        edges(&domain_doc.polygons),
        Some(edges(&profile.polygons)),
        black,
        x,
        y,
    )?;
    let values = sampling::integrate_pixels(
        &pixels,
        mesh.pixels[0],
        mesh.pixels[1],
        plan.windows.clone(),
        materials,
        mesh.minimum,
    )?;
    let mut regions = vec![vec![]; geometry.rectangles.len()];
    for &rid in &geometry.members[domain] {
        let (xs, ys) = &mesh.axes[rid];
        regions[rid] = vec![[0.; 5]; (xs.len() - 1) * (ys.len() - 1)];
    }
    for (q, value) in values.into_iter().enumerate() {
        let (rid, i, j) = plan.locations[q];
        let value=value.ok_or(format!("layer {:?} region {rid} cell ({i},{j}) bounds={:?}: insufficient raster samples or domain/geometry contradiction; increase px/py",layer.name,geometry.rectangles[rid]))?;
        let mut properties = [value[1], value[2], value[3], value[4], value[5]];
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
        let nx = mesh.axes[rid].0.len() - 1;
        regions[rid][j * nx + i] = properties;
    }
    let z = layer.z_mm.as_ref().ok_or("missing layer Z")?;
    let report = json!({"name":layer.name,"type":layer.kind,"status":"equivalent","domain_id":layer.domain_id,
        "domain_source":domain_doc.source,"cell_count":plan.locations.len(),"partial_domain_cells":0,
        "z_mm":{"start_mm":z.start_mm,"end_mm":z.end_mm,"thickness_known":z.thickness_known,"source":z.source},
        "drill_overlays":covering.iter().map(|d|&d.layer.name).collect::<Vec<_>>()});
    Ok((regions, report))
}
