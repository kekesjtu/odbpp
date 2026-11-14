//! Whole-layer expansion: no Python records or intermediate coordinate trees.
use crate::{
    parser::{Diagnostic, Features, Record, Result, Shape, SymbolRef, Unit, warn},
    symbols,
};
use pcb_geometry::{Point, Ring, boolean, curves};
use std::{
    collections::{BTreeMap, HashMap},
    sync::Arc,
};

#[derive(Clone)]
struct Symbol {
    rings: Arc<Vec<Ring>>,
    stroke: Stroke,
}
#[derive(Clone, Copy)]
enum Stroke {
    Round(f64),
    Square(f64),
    Custom,
}

pub struct Engine {
    pub definitions: HashMap<String, Arc<Features>>,
    cache: HashMap<(String, Unit), Option<Symbol>>,
    stack: Vec<String>,
    pub diagnostics: Vec<Diagnostic>,
    pub sagitta: f64,
    pub maximum: usize,
    pub minimum_stroke: f64,
    pub max_depth: usize,
    pub warn_sweeps: bool,
}
impl Engine {
    pub fn new(
        definitions: HashMap<String, Arc<Features>>,
        sagitta: f64,
        maximum: usize,
        minimum_stroke: f64,
        max_depth: usize,
        warn_sweeps: bool,
    ) -> Result<Self> {
        if !sagitta.is_finite()
            || sagitta <= 0.
            || maximum == 0
            || maximum > 1_000_000
            || !minimum_stroke.is_finite()
            || minimum_stroke < 0.
            || max_depth == 0
        {
            return Err("invalid expansion tolerance or resource limit".into());
        }
        Ok(Self {
            definitions,
            cache: HashMap::new(),
            stack: vec![],
            diagnostics: vec![],
            sagitta,
            maximum,
            minimum_stroke,
            max_depth,
            warn_sweeps,
        })
    }
    fn symbol(&mut self, reference: Option<&SymbolRef>, path: &str) -> Result<Option<Symbol>> {
        let Some(reference) = reference else {
            warn(
                &mut self.diagnostics,
                "geometry.symbol_undefined",
                "record has no resolved symbol".into(),
                path,
            );
            return Ok(None);
        };
        let key = (reference.name.clone(), reference.unit);
        if let Some(s) = self.cache.get(&key) {
            return Ok(s.clone());
        }
        let parsed = symbols::parse(&reference.name, reference.unit);
        let result = if let Some(standard) = parsed {
            if self
                .definitions
                .contains_key(&reference.name.to_lowercase())
            {
                warn(
                    &mut self.diagnostics,
                    "symbol.shadows_standard",
                    format!(
                        "{:?} is a standard symbol and takes precedence over its custom definition",
                        reference.name
                    ),
                    path,
                );
            }
            if !symbols::supported(standard.prefix) {
                warn(
                    &mut self.diagnostics,
                    "geometry.symbol_unsupported",
                    format!(
                        "standard symbol family {:?} ({:?}) is not supported",
                        standard.prefix, reference.name
                    ),
                    path,
                );
                None
            } else {
                let stroke = match standard.prefix {
                    "r" => Stroke::Round(standard.size(0) / 2.),
                    "s" => Stroke::Square(standard.size(0)),
                    _ => Stroke::Custom,
                };
                Some(Symbol {
                    rings: Arc::new(standard.region(self.sagitta, self.maximum)?),
                    stroke,
                })
            }
        } else {
            let name = reference.name.to_lowercase();
            if self.stack.contains(&name) {
                let mut chain = self.stack.clone();
                chain.push(name);
                return Err(format!(
                    "user-defined symbol reference cycle: {}",
                    chain.join(" -> ")
                ));
            }
            if self.stack.len() >= self.max_depth {
                return Err(format!(
                    "user-defined symbol {:?} exceeds maximum nesting depth {}",
                    reference.name, self.max_depth
                ));
            }
            if let Some(file) = self.definitions.get(&name).cloned() {
                self.stack.push(name);
                let composed = self.compose(&file, vec![], false);
                self.stack.pop();
                Some(Symbol {
                    rings: Arc::new(composed?),
                    stroke: Stroke::Custom,
                })
            } else {
                warn(
                    &mut self.diagnostics,
                    "geometry.symbol_undefined",
                    format!(
                        "no geometry for symbol {:?}; records using it contribute nothing",
                        reference.name
                    ),
                    path,
                );
                None
            }
        };
        self.cache.insert(key, result.clone());
        Ok(result)
    }
    fn path(&mut self, shape: &Shape, file: &Features) -> Result<Vec<Point>> {
        match shape {
            Shape::Line { start, end, .. } => Ok(vec![*start, *end]),
            Shape::Arc {
                start,
                end,
                center,
                clockwise,
                ..
            } => {
                if (start.0 - center.0).hypot(start.1 - center.1) <= 0. {
                    warn(
                        &mut self.diagnostics,
                        "geometry.arc_degenerate",
                        format!("arc at {start:?} has a coincident centre"),
                        &file.path,
                    );
                    return Ok(vec![]);
                }
                let mut points = vec![*start];
                points.extend(curves::arc_points(
                    *start,
                    *end,
                    *center,
                    *clockwise,
                    self.sagitta,
                    self.maximum,
                )?);
                Ok(points)
            }
            _ => Ok(vec![]),
        }
    }
    fn run(&mut self, records: &[Record], file: &Features) -> Result<Vec<Ring>> {
        let mut rings = vec![];
        // Ordered groups keep both execution and floating-point output deterministic.
        let mut strokes: BTreeMap<(String, &'static str), (Symbol, Vec<Vec<Point>>)> =
            BTreeMap::new();
        for record in records {
            match &record.shape {
                Shape::Surface(surface) => {
                    rings.extend(surface.region(self.sagitta, self.maximum)?)
                }
                Shape::Pad {
                    origin,
                    symbol,
                    resize,
                    angle,
                    mirrored,
                } => {
                    let Some(symbol) = self.symbol(symbol.as_ref(), &file.path)? else {
                        continue;
                    };
                    let resized;
                    let pattern = if *resize != 0. {
                        warn(&mut self.diagnostics,"geometry.resize_unverified","apt_def resize is applied as a dimensional delta in micrometre/mil, offset by half the delta".into(),&file.path);
                        resized = boolean::offset(&symbol.rings, *resize / 2., self.sagitta)?;
                        &resized
                    } else {
                        symbol.rings.as_ref()
                    };
                    let (s, c) = angle.to_radians().sin_cos();
                    for (points, hole) in pattern {
                        let mut points: Vec<Point> = points
                            .iter()
                            .map(|&(x, y)| {
                                let (x, y) = (x * c + y * s, -x * s + y * c);
                                (
                                    x + origin.0,
                                    if *mirrored {
                                        -y + origin.1
                                    } else {
                                        y + origin.1
                                    },
                                )
                            })
                            .collect();
                        if *mirrored {
                            points.reverse();
                        }
                        rings.push((points, *hole));
                    }
                }
                Shape::Line { symbol, .. } | Shape::Arc { symbol, .. } => {
                    let Some(resolved) = self.symbol(symbol.as_ref(), &file.path)? else {
                        continue;
                    };
                    let path = self.path(&record.shape, file)?;
                    if path.len() < 2 {
                        continue;
                    }
                    let reference = symbol.as_ref().unwrap();
                    strokes
                        .entry((reference.name.clone(), reference.unit.name()))
                        .or_insert_with(|| (resolved, vec![]))
                        .1
                        .push(path);
                }
            }
        }
        for ((name, _), (symbol, paths)) in strokes {
            match symbol.stroke {
                Stroke::Round(mut radius) => {
                    if radius <= 0. && self.minimum_stroke > 0. {
                        radius = self.minimum_stroke / 2.;
                        warn(
                            &mut self.diagnostics,
                            "geometry.zero_width_stroke",
                            format!(
                                "zero-width (r0) artwork is drawn at {} mm",
                                self.minimum_stroke
                            ),
                            &file.path,
                        );
                    }
                    rings.extend(boolean::stroke(&paths, radius, self.sagitta)?);
                }
                Stroke::Square(side) => {
                    rings.extend(boolean::sweep(&[symbols::square(side)], &paths)?)
                }
                Stroke::Custom => {
                    if self.warn_sweeps {
                        warn(
                            &mut self.diagnostics,
                            "geometry.sweep_asymmetric",
                            format!(
                                "line/arc drawn with non-round symbol {name:?}; islands and holes are swept independently"
                            ),
                            &file.path,
                        );
                    }
                    for path in paths {
                        let mut swept = vec![];
                        for (pattern, hole) in symbol
                            .rings
                            .iter()
                            .filter(|(_, h)| !h)
                            .chain(symbol.rings.iter().filter(|(_, h)| *h))
                        {
                            let part = boolean::sweep(
                                std::slice::from_ref(pattern),
                                std::slice::from_ref(&path),
                            )?;
                            if *hole {
                                swept =
                                    boolean::operate("difference", &swept, &part, boolean::SCALE)?;
                            } else {
                                swept.extend(part);
                                swept = boolean::operate("union", &swept, &[], boolean::SCALE)?;
                            }
                        }
                        rings.extend(swept);
                    }
                }
            }
        }
        if rings.len() <= 1 {
            Ok(rings)
        } else {
            boolean::operate("union", &rings, &[], boolean::SCALE)
        }
    }
    pub fn drawing(
        &mut self,
        file: Option<&Features>,
        profile: Option<&crate::parser::Surface>,
        invert: bool,
        seed: bool,
    ) -> Result<crate::drawing::Drawing> {
        use crate::drawing::{Drawing, Paint};
        let mut operations = vec![];
        if seed {
            let rings = profile
                .ok_or("drawing has no usable base profile")?
                .region(self.sagitta, self.maximum)?;
            operations.push(Paint {
                positive: true,
                width: 0.,
                points: vec![],
                rings,
            });
        }
        if let Some(file) = file {
            for record in &file.records {
                let positive = record.positive != invert;
                if matches!(record.shape, Shape::Line { .. } | Shape::Arc { .. }) {
                    if let Some(symbol) = self.symbol(record.shape.symbol(), &file.path)? {
                        if let Stroke::Round(radius) = symbol.stroke {
                            let width = if radius > 0. {
                                radius * 2.
                            } else {
                                self.minimum_stroke
                            };
                            if width > 0. {
                                let points = self.path(&record.shape, file)?;
                                if points.len() >= 2 {
                                    operations.push(Paint {
                                        positive,
                                        width,
                                        points,
                                        rings: vec![],
                                    });
                                }
                            }
                            continue;
                        }
                    }
                }
                let rings = self.run(std::slice::from_ref(record), file)?;
                if !rings.is_empty() {
                    operations.push(Paint {
                        positive,
                        width: 0.,
                        points: vec![],
                        rings,
                    });
                }
            }
        }
        Ok(Drawing::new(operations))
    }
    pub fn compose(
        &mut self,
        file: &Features,
        mut base: Vec<Ring>,
        invert: bool,
    ) -> Result<Vec<Ring>> {
        let mut start = 0;
        while start < file.records.len() {
            let positive = file.records[start].positive;
            let mut end = start + 1;
            while end < file.records.len() && file.records[end].positive == positive {
                end += 1;
            }
            let run = self.run(&file.records[start..end], file)?;
            if !run.is_empty() {
                if positive != invert {
                    if base.is_empty() {
                        base = run;
                    } else {
                        base.extend(run);
                        base = boolean::operate("union", &base, &[], boolean::SCALE)?;
                    }
                } else if !base.is_empty() {
                    base = boolean::operate("difference", &base, &run, boolean::SCALE)?;
                }
            }
            start = end;
        }
        Ok(base)
    }
}
