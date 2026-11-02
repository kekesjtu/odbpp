//! Topology and protected physical cores; immutable source preparation is reused.
use crate::geos::{Result, Shape};
use serde_json::{Value, json};

pub struct Source<'a> {
    pub components: Vec<Shape<'a>>,
    holes: Vec<Shape<'a>>,
    cores: Vec<(f64, Vec<Shape<'a>>)>,
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
    pub fn new(original: &Shape<'a>) -> Result<Self> {
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
        Ok(Self {
            components,
            holes,
            cores,
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
}
