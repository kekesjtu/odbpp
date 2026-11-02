use crate::{
    coarsen::{Band, Rect, coarsen, pack, unique},
    document,
    geos::{Result, Shape},
    options::Options,
    profile,
    topology::Source,
};
use serde_json::{Value, json};

pub struct Plan {
    pub rectangles: Vec<Rect>,
    pub audit: Value,
    pub ok: bool,
}
fn make_band(
    original: &Shape<'_>,
    rings: &[Vec<(f64, f64)>],
    a: f64,
    b: f64,
    depth: usize,
) -> Result<Band> {
    let spans = profile::common_y(rings.to_vec(), a, b)?;
    let bounds = original.bounds()?;
    let actual = original.intersection(&original.engine.rectangle([
        a,
        bounds[1] - 1.,
        b,
        bounds[3] + 1.,
    ])?)?;
    let modeled = original
        .engine
        .rectangles(&spans.iter().map(|&(l, h)| [a, l, b, h]).collect::<Vec<_>>())?;
    Ok(Band {
        a,
        b,
        spans,
        depth,
        loss: (actual.area()? - modeled.area()?).max(0.),
    })
}
pub fn rectify(
    original: &Shape<'_>,
    options: &Options,
    basis: [f64; 4],
    planning_ratio: f64,
    refs: &[Vec<f64>; 2],
) -> Result<Plan> {
    if original.empty()? {
        return Err("empty profile".into());
    }
    let source = Source::new(original)?;
    let rings = document::polygons(original)?
        .iter()
        .map(|p| p.points.clone())
        .collect::<Vec<_>>();
    let bounds = original.bounds()?;
    let step = options.tolerance_mm.unwrap_or(0.25);
    let orthogonal = rings.iter().all(|p| {
        p.iter()
            .zip(p.iter().cycle().skip(1))
            .all(|(a, b)| (a.0 - b.0).abs() < 1e-9 || (a.1 - b.1).abs() < 1e-9)
    });
    let breaks = if orthogonal {
        unique(rings.iter().flatten().map(|p| p.0))
    } else {
        let requested = (basis[2] - basis[0]) / step;
        if !requested.is_finite() || requested > 10000. {
            return Err(format!("initial band resource limit: {requested}"));
        }
        let count = (requested.ceil() as usize).max(1);
        (0..=count)
            .map(|i| basis[0] + (basis[2] - basis[0]) * i as f64 / count as f64)
            .collect()
    };
    let mut bands = breaks
        .windows(2)
        .map(|p| make_band(original, &rings, p[0], p[1], 0))
        .collect::<Result<Vec<_>>>()?;
    let original_area = original.area()?;
    let allowed = planning_ratio * original_area;
    let mut history = vec![];
    let mut termination = "constraints_satisfied".to_owned();
    for iteration in 0..=10000 {
        let model = original.engine.rectangles(&pack(&bands))?;
        let lost = bands.iter().map(|p| p.loss).sum::<f64>();
        let mut reason = "area";
        if lost <= allowed + 1e-8 {
            if source.topology(&model)?["preserved"] == true {
                break;
            }
            reason = "topology";
        }
        let mut eligible: Vec<_> = (0..bands.len())
            .filter(|&i| reason != "topology" || (bands[i].spans.is_empty() && bands[i].loss > 0.))
            .collect();
        if eligible.is_empty() {
            eligible = (0..bands.len()).collect();
        }
        let &i = eligible
            .iter()
            .max_by(|&&i, &&j| {
                bands[i]
                    .loss
                    .total_cmp(&bands[j].loss)
                    .then(bands[j].a.total_cmp(&bands[i].a))
            })
            .ok_or("profile has no intervals")?;
        let worst = &bands[i];
        if orthogonal
            || (allowed == 0. && !orthogonal)
            || worst.depth >= 20
            || (worst.b - worst.a) / 2. < 1e-5
            || iteration == 10000
            || worst.loss <= 1e-12
        {
            termination = format!(
                "cannot refine {reason}: X=[{},{}] mm, length={}, loss={lost}, budget={allowed}, depth={}, steps={iteration}",
                worst.a,
                worst.b,
                worst.b - worst.a,
                worst.depth
            );
            break;
        }
        history.push(
            json!({"iteration":iteration,"reason":reason,"x_interval_mm":[worst.a,worst.b],
            "band_count":bands.len(),"lost_area_mm2":lost,"selected_loss_mm2":worst.loss}),
        );
        let mid = (worst.a + worst.b) / 2.;
        let (a, b, depth) = (worst.a, worst.b, worst.depth);
        bands.splice(
            i..i + 1,
            [
                make_band(original, &rings, a, mid, depth + 1)?,
                make_band(original, &rings, mid, b, depth + 1)?,
            ],
        );
    }
    let model = original.engine.rectangles(&pack(&bands))?;
    if source.topology(&model)?["preserved"] == true
        && original.difference(&model)?.area()? <= allowed + 1e-8
        && model.difference(original)?.area()? <= 1e-8
    {
        bands = coarsen(bands, original, &source, allowed, refs)?;
    }
    let rectangles = pack(&bands);
    let model = original.engine.rectangles(&rectangles)?;
    let topology = source.topology(&model)?;
    let lost = original.difference(&model)?.area()?;
    let outside = model.difference(original)?.area()?;
    let ok = topology["preserved"] == true && lost <= allowed + 1e-8 && outside <= 1e-8;
    let audit = json!({"strategy":"inscribed-vertical-staircase-worst-first","ok":ok,"tolerance_mm":step,
        "original_area_mm2":original_area,"rectified_area_mm2":model.area()?,"lost_area_mm2":lost,
        "gained_area_mm2":outside,"relative_area_error":lost/original_area,"rectangle_count":rectangles.len(),
        "audit":{"inscribed":outside<=1e-8,"area_budget_mm2":allowed,"relative_area_budget":options.max_area_loss_ratio,
        "planning_area_loss_ratio":planning_ratio,"y_boundaries":"true_common_intervals","y_quantization":false,
        "min_step_mm":1e-5,"max_refinement_depth":20,"max_refinement_steps":10000,"max_initial_bands":10000,
        "initial_seed_cap_mm":0.25,"refinement_priority":"largest area loss, with topology protection",
        "termination":termination,"refinement_count":history.len(),"local_refinement":history,"topology":topology}});
    if !ok {
        return Err(format!(
            "inscribed geometry failed for bounds={bounds:?}: loss={lost}, budget={allowed}, audit={audit}"
        ));
    }
    Ok(Plan {
        rectangles,
        audit,
        ok,
    })
}
