//! Continuous contour and topology checks; immutable source preparation is reused.
use crate::geos::{Result, Shape};
use serde_json::{Value, json};

pub struct Source<'a> {
    pub components: Vec<Shape<'a>>,
    holes: Vec<Shape<'a>>,
    cores: Vec<(f64, Vec<Shape<'a>>)>,
    rings: Vec<Vec<Shape<'a>>>,
    tubes: Vec<Vec<Shape<'a>>>,
    limit: f64,
}
pub struct Contour {
    pub within: bool,
    pub pairs: usize,
    pub lower: f64,
    pub upper: f64,
    pub location: Option<(f64, f64)>,
}
impl Contour {
    pub fn json(&self, limit: f64) -> Value {
        json!({"metric":"symmetric_euclidean_corresponding_contours",
        "within_limit":self.within,"contour_pairs":self.pairs,"max_allowed_mm":limit,
        "max_distance_lower_mm":if self.lower.is_finite(){Some(self.lower)}else{None},
        "max_distance_upper_mm":if self.upper.is_finite(){Some(self.upper)}else{None},
        "worst_location_mm":self.location,"measurement_resolution_mm":(limit*0.001).max(1e-9),
        "verification":"continuous two-sided inscribed polygonal distance tubes"})
    }
}
pub fn covers_roundoff(outer: &Shape<'_>, inner: &Shape<'_>) -> Result<bool> {
    if outer.covers(inner)? {
        return Ok(true);
    }
    let slack = outer
        .bounds()?
        .into_iter()
        .chain(inner.bounds()?)
        .map(|v| (v.next_up() - v).abs())
        .fold(0., f64::max)
        * 32.;
    outer.buffer(slack)?.covers(inner)
}
impl<'a> Source<'a> {
    pub fn new(original: &Shape<'a>, limit: f64) -> Result<Self> {
        let components = original.components()?;
        let rings = components
            .iter()
            .map(Shape::rings)
            .collect::<Result<Vec<_>>>()?;
        let mut holes = vec![];
        for component in &rings {
            for h in component.iter().skip(1) {
                holes.push(original.engine.polygon(&h.points()?, &[])?);
            }
        }
        let mut cores = vec![];
        for radius in [0.03125, 0.1, 0.25, 0.5, 1.] {
            cores.push((radius, original.buffer(-radius)?.components()?));
        }
        let tubes = rings
            .iter()
            .map(|r| r.iter().map(|r| r.buffer(limit)).collect())
            .collect::<Result<Vec<Vec<_>>>>()?;
        Ok(Self {
            components,
            holes,
            cores,
            rings,
            tubes,
            limit,
        })
    }
    pub fn topology(&self, model: &Shape<'a>) -> Result<Value> {
        let targets = model.components()?;
        let points = targets
            .iter()
            .map(Shape::point)
            .collect::<Result<Vec<_>>>()?;
        let mut matches = vec![];
        for s in &self.components {
            let mut n = 0;
            for p in &points {
                n += usize::from(s.covers(p)?);
            }
            matches.push(n);
        }
        let mut holes = vec![];
        let mut positive = true;
        for target in &targets {
            positive &= target.buffer(-1e-7)?.components()?.len() == 1;
            for h in target.rings()?.iter().skip(1) {
                holes.push(model.engine.polygon(&h.points()?, &[])?);
            }
        }
        let mut hole_matches = vec![];
        for h in &self.holes {
            let mut n = 0;
            for m in &holes {
                n += usize::from(covers_roundoff(m, h)?);
            }
            hole_matches.push(n);
        }
        let mut checks = vec![];
        let mut cores_kept = true;
        for (radius, cores) in &self.cores {
            let mut kept = 0;
            for core in cores {
                let overlap = core.intersection(model)?;
                kept += usize::from(!overlap.empty()? && overlap.area()? > 1e-10);
            }
            cores_kept &= kept == cores.len();
            checks.push(json!({"erosion_mm":radius,"cores":cores.len(),"retained":kept}));
        }
        let preserved = matches.iter().chain(&hole_matches).all(|&n| n == 1)
            && targets.len() == self.components.len()
            && holes.len() == self.holes.len()
            && positive
            && cores_kept;
        Ok(
            json!({"original_components":self.components.len(),"model_components":targets.len(),
            "component_correspondence":matches,"original_holes":self.holes.len(),"model_holes":holes.len(),
            "hole_correspondence":hole_matches,"positive_width_connections":positive,
            "hole_containment_roundoff_ulps":32,"protected_core_checks":checks,"preserved":preserved}),
        )
    }
    pub fn contour(&self, model: &Shape<'a>, measure: bool) -> Result<Contour> {
        let targets = model.components()?;
        let mut out = Contour {
            within: true,
            pairs: 0,
            lower: 0.,
            upper: 0.,
            location: None,
        };
        if targets.len() != self.components.len() || targets.is_empty() {
            out.within = false;
            return Ok(out);
        }
        for (index, original) in self.components.iter().enumerate() {
            let mut matches = vec![];
            for t in &targets {
                if original.covers(&t.point()?)? {
                    matches.push(t);
                }
            }
            if matches.len() != 1 {
                out.within = false;
                out.pairs = 0;
                return Ok(out);
            }
            let rings = matches[0].rings()?;
            if rings.len() != self.rings[index].len() {
                out.within = false;
                out.pairs = 0;
                return Ok(out);
            }
            let mut pairs = vec![(0, 0)];
            for (i, source) in self.rings[index].iter().enumerate().skip(1) {
                let hole = model.engine.polygon(&source.points()?, &[])?;
                let mut candidates = vec![];
                for (j, r) in rings.iter().enumerate().skip(1) {
                    if covers_roundoff(&model.engine.polygon(&r.points()?, &[])?, &hole)? {
                        candidates.push(j);
                    }
                }
                if candidates.len() != 1 {
                    out.within = false;
                    out.pairs = 0;
                    return Ok(out);
                }
                pairs.push((i, candidates[0]));
            }
            for (i, j) in pairs {
                let (a, b) = (&self.rings[index][i], &rings[j]);
                out.pairs += 1;
                let within = if a.equals(b)? {
                    true
                } else {
                    a.hausdorff(b)? <= self.limit
                        && b.buffer(self.limit)?.covers(a)?
                        && self.tubes[index][i].covers(b)?
                };
                out.within &= within;
                if measure {
                    let (low, high) = line_bounds(a, b, self.limit)?;
                    out.lower = out.lower.max(low);
                    if high > out.upper {
                        out.upper = high;
                        if high > self.limit {
                            out.location = witness(a, b, high, self.limit)?;
                        }
                    }
                }
            }
        }
        Ok(out)
    }
}
pub fn line_within(a: &Shape<'_>, b: &Shape<'_>, limit: f64) -> Result<bool> {
    if a.equals(b)? {
        return Ok(true);
    }
    if a.empty()? || b.empty()? || a.hausdorff(b)? > limit {
        return Ok(false);
    }
    Ok(b.buffer(limit)?.covers(a)? && a.buffer(limit)?.covers(b)?)
}
pub fn line_bounds(a: &Shape<'_>, b: &Shape<'_>, limit: f64) -> Result<(f64, f64)> {
    if a.equals(b)? {
        return Ok((0., 0.));
    }
    if a.empty()? || b.empty()? {
        return Ok((f64::INFINITY, f64::INFINITY));
    }
    let vertex = a.hausdorff(b)?;
    let mut low = vertex;
    let mut high = if line_within(a, b, limit)? {
        limit
    } else {
        (limit * 2.).max(low * 1.01)
    };
    let mut verified = false;
    for _ in 0..64 {
        if line_within(a, b, high)? {
            verified = true;
            break;
        }
        high *= 2.;
    }
    if !verified {
        return Err("cannot certify contour distance".into());
    }
    for _ in 0..48 {
        if high - low <= (limit * 0.001).max(1e-9) {
            break;
        }
        let mid = (low + high) / 2.;
        if line_within(a, b, mid)? {
            high = mid;
        } else {
            low = mid;
        }
    }
    Ok((vertex, high))
}
pub fn witness(a: &Shape<'_>, b: &Shape<'_>, upper: f64, limit: f64) -> Result<Option<(f64, f64)>> {
    if !upper.is_finite() {
        return Ok(None);
    }
    let threshold = (upper - (limit * 0.002).max(1e-9)).max(0.);
    let mut worst = None;
    for (source, target) in [(a, b), (b, a)] {
        for part in source.difference(&target.buffer(threshold)?)?.parts()? {
            let points = part.points()?;
            let mut samples = points.clone();
            samples.extend(
                points
                    .windows(2)
                    .map(|p| ((p[0].0 + p[1].0) / 2., (p[0].1 + p[1].1) / 2.)),
            );
            for (x, y) in samples {
                let point = a.engine.rectangle([x, y, x, y])?;
                let d = point.distance(target)?;
                if worst.is_none_or(|(prior, _, _)| d > prior) {
                    worst = Some((d, x, y));
                }
            }
        }
    }
    Ok(worst.map(|(_, x, y)| (x, y)))
}
