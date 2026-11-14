//! Topology and protected physical cores; immutable source preparation is reused.
use crate::geometry::Shape;
use serde_json::{Value, json};

pub struct Source {
    components: Vec<Shape>,
    holes: Vec<Shape>,
    cores: Vec<(f64, Vec<Shape>)>,
}

fn correspondence(sources: &[Shape], targets: &[Shape]) -> (Vec<usize>, Vec<usize>) {
    let mut forward = vec![0; sources.len()];
    let mut reverse = vec![0; targets.len()];
    for (i, source) in sources.iter().enumerate() {
        for (j, target) in targets.iter().enumerate() {
            if source.intersection(target).area() > 1e-10 {
                forward[i] += 1;
                reverse[j] += 1;
            }
        }
    }
    (forward, reverse)
}
impl Source {
    pub fn new(original: &Shape) -> Self {
        let components = original.components();
        let holes = original.holes();
        let mut cores = vec![];
        for radius in [0.03125, 0.1, 0.25, 0.5, 1.] {
            cores.push((radius, original.buffer(-radius).components()));
        }
        Self {
            components,
            holes,
            cores,
        }
    }
    pub fn topology(&self, model: &Shape) -> Value {
        let targets = model.components();
        let (matches, reverse_matches) = correspondence(&self.components, &targets);
        let mut holes = vec![];
        let mut positive = true;
        for target in &targets {
            positive &= target.buffer(-1e-7).components().len() == 1;
            holes.extend(target.holes());
        }
        let (hole_matches, reverse_hole_matches) = correspondence(&self.holes, &holes);
        let components_preserved = matches.iter().chain(&reverse_matches).all(|&n| n == 1)
            && targets.len() == self.components.len();
        let holes_preserved = hole_matches
            .iter()
            .chain(&reverse_hole_matches)
            .all(|&n| n == 1)
            && holes.len() == self.holes.len();
        json!({"components":{"original_count":self.components.len(),"model_count":targets.len(),
                "forward_matches":matches,"reverse_matches":reverse_matches,"preserved":components_preserved},
            "holes":{"original_count":self.holes.len(),"model_count":holes.len(),
                "forward_matches":hole_matches,"reverse_matches":reverse_hole_matches,"preserved":holes_preserved},
            "positive_width_connections":positive,
            "preserved":components_preserved && holes_preserved && positive})
    }

    // An additional local-coverage constraint, deliberately absent from diagnostics.
    pub fn protected_cores_preserved(&self, model: &Shape) -> bool {
        self.cores.iter().all(|(_, cores)| {
            cores.iter().all(|core| {
                let overlap = core.intersection(model);
                !overlap.empty() && overlap.area() > 1e-10
            })
        })
    }
}
