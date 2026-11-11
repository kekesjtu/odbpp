//! Topology and protected physical cores; immutable source preparation is reused.
use crate::geos::{Result, Shape};
use serde_json::{Value, json};

pub struct Source<'a> {
    components: Vec<Shape<'a>>,
    holes: Vec<Shape<'a>>,
    cores: Vec<(f64, Vec<Shape<'a>>)>,
}

fn correspondence(
    sources: &[Shape<'_>],
    targets: &[Shape<'_>],
) -> Result<(Vec<usize>, Vec<usize>)> {
    let mut forward = vec![0; sources.len()];
    let mut reverse = vec![0; targets.len()];
    for (i, source) in sources.iter().enumerate() {
        for (j, target) in targets.iter().enumerate() {
            if source.intersection(target)?.area()? > 1e-10 {
                forward[i] += 1;
                reverse[j] += 1;
            }
        }
    }
    Ok((forward, reverse))
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
        let (matches, reverse_matches) = correspondence(&self.components, &targets)?;
        let mut holes = vec![];
        let mut positive = true;
        for target in &targets {
            positive &= target.buffer(-1e-7)?.components()?.len() == 1;
            for h in target.rings()?.iter().skip(1) {
                holes.push(model.engine.polygon(&h.points()?, &[])?);
            }
        }
        let (hole_matches, reverse_hole_matches) = correspondence(&self.holes, &holes)?;
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
        let preserved = matches
            .iter()
            .chain(&hole_matches)
            .chain(&reverse_matches)
            .chain(&reverse_hole_matches)
            .all(|&n| n == 1)
            && targets.len() == self.components.len()
            && holes.len() == self.holes.len()
            && positive
            && cores_kept;
        Ok(
            json!({"original_components":self.components.len(),"model_components":targets.len(),
            "component_correspondence":matches,"reverse_component_correspondence":reverse_matches,
            "original_holes":self.holes.len(),"model_holes":holes.len(),
            "hole_correspondence":hole_matches,"positive_width_connections":positive,
            "reverse_hole_correspondence":reverse_hole_matches,
            "correspondence":"positive-area overlap","protected_core_checks":checks,"preserved":preserved}),
        )
    }
}
