//! Coarse PyO3 boundary. Handles share native data, never Python geometry.
use crate::{
    compose::Engine,
    geometry::{Point, Ring, boolean, curves},
    labeling::Font,
    parser::{self, Features, Shape, Surface, Unit},
    symbols,
};
use pyo3::{
    exceptions::{PyIndexError, PyValueError},
    prelude::*,
    types::PyDict,
};
use std::{
    collections::{BTreeMap, HashMap},
    sync::Arc,
};

fn value_error(e: String) -> PyErr {
    PyValueError::new_err(e)
}
fn bounds(points: impl Iterator<Item = Point>) -> Option<(f64, f64, f64, f64)> {
    points.fold(None, |b, p| {
        Some(match b {
            None => (p.0, p.1, p.0, p.1),
            Some((x0, y0, x1, y1)) => (x0.min(p.0), y0.min(p.1), x1.max(p.0), y1.max(p.1)),
        })
    })
}

#[pyclass(name = "Ring", frozen, module = "pcbkit_rust._pcbkit_native")]
#[derive(Clone)]
pub struct NativeRing {
    data: Arc<Vec<Ring>>,
    index: usize,
    area: f64,
    bounds: Option<(f64, f64, f64, f64)>,
}
#[pymethods]
impl NativeRing {
    #[getter]
    fn points(&self) -> Vec<Point> {
        self.data[self.index].0.clone()
    }
    #[getter]
    fn is_hole(&self) -> bool {
        self.data[self.index].1
    }
    #[getter]
    fn area(&self) -> f64 {
        self.area
    }
    #[getter]
    fn bounds(&self) -> Option<(f64, f64, f64, f64)> {
        self.bounds
    }
    fn __len__(&self) -> usize {
        self.data[self.index].0.len()
    }
}
#[pyclass(name = "Region", frozen, module = "pcbkit_rust._pcbkit_native")]
#[derive(Clone)]
pub struct NativeRegion {
    data: Arc<Vec<Ring>>,
    rings: Arc<Vec<NativeRing>>,
    area: f64,
    bounds: Option<(f64, f64, f64, f64)>,
}
impl NativeRegion {
    pub fn from(rings: Vec<Ring>) -> Self {
        let area = rings
            .iter()
            .map(|(p, h)| curves::signed_area(p).abs() * if *h { -1. } else { 1. })
            .sum();
        let bbox = bounds(rings.iter().flat_map(|(p, _)| p.iter().copied()));
        // All ring handles share the coordinate allocation; preview alone reads points.
        let data = Arc::new(rings);
        let handles = data
            .iter()
            .enumerate()
            .map(|(index, r)| NativeRing {
                area: curves::signed_area(&r.0).abs(),
                bounds: bounds(r.0.iter().copied()),
                data: data.clone(),
                index,
            })
            .collect();
        Self {
            data,
            rings: Arc::new(handles),
            area,
            bounds: bbox,
        }
    }
}
#[pymethods]
impl NativeRegion {
    #[new]
    fn new() -> Self {
        Self::from(vec![])
    }
    #[staticmethod]
    fn from_rings(py: Python<'_>, rings: Vec<Ring>) -> PyResult<Self> {
        py.detach(|| boolean::operate("union", &rings, &[], boolean::SCALE))
            .map(Self::from)
            .map_err(value_error)
    }
    #[getter]
    fn rings(&self) -> Vec<NativeRing> {
        self.rings.as_ref().clone()
    }
    #[getter]
    fn area(&self) -> f64 {
        self.area
    }
    #[getter]
    fn bounds(&self) -> Option<(f64, f64, f64, f64)> {
        self.bounds
    }
    fn __bool__(&self) -> bool {
        !self.data.is_empty()
    }
    fn __len__(&self) -> usize {
        self.data.len()
    }
    fn polygons(&self, py: Python<'_>) -> PyResult<Vec<Py<PyDict>>> {
        self.data
            .iter()
            .map(|(points, hole)| {
                let d = PyDict::new(py);
                d.set_item("island", !*hole)?;
                d.set_item("points", points)?;
                Ok(d.unbind())
            })
            .collect()
    }
    fn same_geometry(&self, py: Python<'_>, other: &NativeRegion) -> PyResult<bool> {
        if self.bounds != other.bounds {
            return Ok(false);
        }
        py.detach(|| boolean::operate("xor", &self.data, &other.data, boolean::SCALE))
            .map(|rings| rings.is_empty())
            .map_err(value_error)
    }
    fn snap(&self, py: Python<'_>, digits: u32) -> PyResult<(Self, usize)> {
        py.detach(|| boolean::snap(&self.data, 10f64.powf(digits as f64)))
            .map(|(r, n)| (Self::from(r), n))
            .map_err(value_error)
    }
}

#[pyfunction]
pub fn region_boolean(
    py: Python<'_>,
    operation: &str,
    regions: Vec<PyRef<'_, NativeRegion>>,
) -> PyResult<NativeRegion> {
    let data: Vec<_> = regions.iter().map(|r| r.data.clone()).collect();
    py.detach(|| -> parser::Result<Vec<Ring>> {
        if data.is_empty() {
            return Ok(vec![]);
        }
        if operation == "union" {
            let all: Vec<_> = data.iter().flat_map(|r| r.iter().cloned()).collect();
            return boolean::operate("union", &all, &[], boolean::SCALE);
        }
        let mut result = data[0].as_ref().clone();
        for clip in &data[1..] {
            result = boolean::operate(operation, &result, clip, boolean::SCALE)?;
        }
        Ok(result)
    })
    .map(NativeRegion::from)
    .map_err(value_error)
}

#[pyclass(name = "Profile", frozen, module = "pcbkit_rust._pcbkit_native")]
#[derive(Clone)]
pub struct NativeProfile {
    data: Arc<Surface>,
}
#[pymethods]
impl NativeProfile {
    #[getter]
    fn polygon_count(&self) -> usize {
        self.data.contours.len()
    }
    fn __bool__(&self) -> bool {
        !self.data.contours.is_empty()
    }
    #[pyo3(signature=(board,sagitta=0.005,maximum=4096))]
    fn layer_region(
        &self,
        py: Python<'_>,
        board: Option<&NativeProfile>,
        sagitta: f64,
        maximum: usize,
    ) -> PyResult<NativeRegion> {
        py.detach(|| -> parser::Result<_> {
            let mut rings = self.data.region(sagitta, maximum)?;
            if let Some(board) = board {
                let holes = Surface {
                    contours: board
                        .data
                        .contours
                        .iter()
                        .filter(|c| c.hole)
                        .cloned()
                        .map(|mut c| {
                            c.hole = false;
                            c
                        })
                        .collect(),
                };
                let holes = holes.region(sagitta, maximum)?;
                if !holes.is_empty() {
                    rings = boolean::operate("difference", &rings, &holes, boolean::SCALE)?;
                }
            }
            Ok(NativeRegion::from(rings))
        })
        .map_err(value_error)
    }
    #[pyo3(signature=(sagitta=0.005,maximum=4096))]
    fn region(&self, py: Python<'_>, sagitta: f64, maximum: usize) -> PyResult<NativeRegion> {
        py.detach(|| self.data.region(sagitta, maximum))
            .map(NativeRegion::from)
            .map_err(value_error)
    }
}

#[pyclass(name = "Features", frozen, module = "pcbkit_rust._pcbkit_native")]
#[derive(Clone)]
pub struct NativeFeatures {
    pub data: Arc<Features>,
}
#[pymethods]
impl NativeFeatures {
    #[staticmethod]
    #[pyo3(signature=(text,path,unit,definitions=None,require_units=false))]
    fn parse(
        py: Python<'_>,
        text: &str,
        path: &str,
        unit: &str,
        definitions: Option<HashMap<String, String>>,
        require_units: bool,
    ) -> PyResult<Self> {
        let unit = Unit::parse(unit).map_err(value_error)?;
        py.detach(|| {
            parser::parse(
                text,
                path,
                unit,
                &definitions.unwrap_or_default(),
                require_units,
            )
        })
        .map(|data| Self { data })
        .map_err(value_error)
    }
    #[getter]
    fn actual_count(&self) -> usize {
        self.data.records.len()
    }
    #[getter]
    fn declared_count(&self) -> Option<usize> {
        self.data.declared_count
    }
    #[getter]
    fn unit(&self) -> &'static str {
        self.data.unit.name()
    }
    #[getter]
    fn symbols(&self) -> Vec<(usize, String, &'static str)> {
        self.data
            .symbols
            .iter()
            .map(|s| (s.index, s.name.clone(), s.unit.name()))
            .collect()
    }
    #[getter]
    fn surface_count(&self) -> usize {
        self.data
            .records
            .iter()
            .filter(|r| matches!(r.shape, Shape::Surface(_)))
            .count()
    }
    fn counts_by_kind(&self) -> BTreeMap<&'static str, usize> {
        let mut counts = BTreeMap::new();
        for r in &self.data.records {
            *counts.entry(r.shape.kind()).or_default() += 1;
        }
        counts
    }
    fn histogram_json(&self) -> String {
        self.data.histogram().to_string()
    }
    fn diagnostics_json(&self) -> String {
        serde_json::to_string(&self.data.diagnostics).unwrap()
    }
    fn raw_records(&self) -> Vec<String> {
        self.data.records.iter().map(|r| r.raw.clone()).collect()
    }
    fn custom_references(&self) -> Vec<String> {
        self.data.custom_references()
    }
    fn font_references(&self) -> std::collections::BTreeSet<&str> {
        self.data.records.iter().filter_map(|record| match &record.shape {
            Shape::Label(label) if label.kind.uses_font() => Some(label.font.as_str()),
            _ => None,
        }).collect()
    }
    fn profile(&self) -> PyResult<NativeProfile> {
        self.data
            .profile()
            .map(|s| NativeProfile { data: Arc::new(s) })
            .map_err(value_error)
    }
    fn record_info_json(&self, index: usize) -> PyResult<String> {
        let r = self
            .data
            .records
            .get(index)
            .ok_or_else(|| PyIndexError::new_err(index))?;
        let mut v = serde_json::json!({"kind":r.shape.kind(),"positive":r.positive,"line":r.line,"uid":r.uid,"dcode":r.dcode,"attributes":r.attributes});
        if let Some(s) = r.shape.symbol() {
            v["symbol"] = serde_json::json!({"index":s.index,"name":s.name,"units":s.unit.name()});
        }
        match &r.shape {
            Shape::Pad {
                resize,
                placement,
                ..
            } => {
                v["origin_mm"] = serde_json::json!(placement.origin);
                v["resize_mm"] = (*resize).into();
                v["angle"] = placement.angle.into();
                v["mirrored"] = placement.mirrored.into();
            }
            Shape::Line { start, end, .. } | Shape::Arc { start, end, .. } => {
                v["start_mm"] = serde_json::json!(start);
                v["end_mm"] = serde_json::json!(end);
            }
            Shape::Label(label) => {
                v["origin_mm"] = serde_json::json!(label.placement.origin);
                v["angle"] = label.placement.angle.into();
                v["mirrored"] = label.placement.mirrored.into();
                v["size_mm"] = serde_json::json!(label.size);
                v["font"] = label.font.clone().into();
                v["text"] = label.value.clone().into();
            }
            _ => {}
        }
        Ok(v.to_string())
    }
}

#[pyfunction]
pub fn describe_symbol(name: &str, unit: &str) -> PyResult<Option<String>> {
    let Some(s) = symbols::parse(name, Unit::parse(unit).map_err(value_error)?) else {
        return Ok(None);
    };
    let mut params: BTreeMap<_, _> = s
        .names
        .iter()
        .copied()
        .zip(s.numbers.iter().copied())
        .collect();
    if s.radius != 0. {
        params.insert("radius", s.radius);
    }
    if s.chamfer != 0. {
        params.insert("chamfer", s.chamfer);
    }
    Ok(Some(serde_json::json!({"prefix":s.prefix,"params":params,"unit":s.unit.name(),"supported":true}).to_string()))
}

#[pyfunction]
pub fn symbol_cycles(definitions: HashMap<String, PyRef<'_, NativeFeatures>>) -> Vec<Vec<String>> {
    fn visit(
        name: &str,
        files: &HashMap<String, Arc<Features>>,
        stack: &mut Vec<String>,
        done: &mut std::collections::HashSet<String>,
        cycles: &mut Vec<Vec<String>>,
    ) {
        if let Some(i) = stack.iter().position(|n| n == name) {
            let mut cycle = stack[i..].to_vec();
            cycle.push(name.into());
            cycles.push(cycle);
            return;
        }
        if !done.insert(name.into()) {
            return;
        }
        if let Some(file) = files.get(name) {
            stack.push(name.into());
            for child in file.custom_references() {
                visit(&child.to_lowercase(), files, stack, done, cycles);
            }
            stack.pop();
        }
    }
    let files: HashMap<_, _> = definitions
        .into_iter()
        .map(|(k, v)| (k.to_lowercase(), v.data.clone()))
        .collect();
    let mut keys: Vec<_> = files.keys().cloned().collect();
    keys.sort();
    let mut cycles = vec![];
    let mut done = std::collections::HashSet::new();
    for name in keys {
        visit(&name, &files, &mut vec![], &mut done, &mut cycles);
    }
    cycles
}

#[pyclass(name = "Drawing", frozen, module = "pcbkit_rust._pcbkit_native")]
pub struct NativeDrawing {
    data: crate::drawing::Drawing,
}
#[pymethods]
impl NativeDrawing {
    fn __bool__(&self) -> bool {
        !self.data.operations.is_empty()
    }
    #[getter]
    fn bounds(&self) -> Option<(f64, f64, f64, f64)> {
        self.data.bounds
    }
    fn svg(&self, py: Python<'_>) -> String {
        py.detach(|| self.data.svg())
    }
    fn commands(&self) -> Vec<(bool, f64, Vec<Point>, Vec<Ring>)> {
        self.data
            .operations
            .iter()
            .map(|op| (op.positive, op.width, op.points.clone(), op.rings.clone()))
            .collect()
    }
}

#[pyclass(name = "Font", frozen, module = "pcbkit_rust._pcbkit_native")]
struct NativeFont {
    data: Arc<Font>,
}
#[pymethods]
impl NativeFont {
    #[staticmethod]
    fn parse(py: Python<'_>, text: &str, path: &str) -> PyResult<Self> {
        py.detach(|| Font::parse(text, path))
            .map(|font| Self { data: Arc::new(font) })
            .map_err(value_error)
    }
}

#[pyclass(name = "Composer", module = "pcbkit_rust._pcbkit_native")]
pub struct Composer {
    engine: Engine,
}
#[pymethods]
impl Composer {
    #[new]
    #[pyo3(signature=(definitions,sagitta=0.005,maximum=4096,minimum_stroke=0.,max_depth=16,warn_sweeps=true,*,fonts=None))]
    fn new(
        definitions: HashMap<String, PyRef<'_, NativeFeatures>>,
        sagitta: f64,
        maximum: usize,
        minimum_stroke: f64,
        max_depth: usize,
        warn_sweeps: bool,
        fonts: Option<HashMap<String, PyRef<'_, NativeFont>>>,
    ) -> PyResult<Self> {
        let definitions = definitions
            .into_iter()
            .map(|(k, v)| (k.to_lowercase(), v.data.clone()))
            .collect();
        let mut engine = Engine::new(
            definitions,
            sagitta,
            maximum,
            minimum_stroke,
            max_depth,
            warn_sweeps,
        )
        .map_err(value_error)?;
        engine.labels.fonts = fonts.unwrap_or_default().into_iter()
            .map(|(name, font)| (name.to_lowercase(), font.data.clone())).collect();
        Ok(Self { engine })
    }
    #[pyo3(signature=(features,profile,invert,seed,digits,*,variables=None))]
    fn layer(
        &mut self,
        py: Python<'_>,
        features: Option<&NativeFeatures>,
        profile: Option<&NativeRegion>,
        invert: bool,
        seed: bool,
        digits: u32,
        variables: Option<HashMap<String, String>>,
    ) -> PyResult<(NativeRegion, usize, usize)> {
        py.detach(|| -> parser::Result<_> {
            self.engine.set_label_variables(variables.unwrap_or_default());
            let base = if seed {
                profile
                    .ok_or("layer has no usable base profile")?
                    .data
                    .as_ref()
                    .clone()
            } else {
                vec![]
            };
            let rings = match features {
                Some(f) => self.engine.compose(&f.data, base, invert)?,
                None => base,
            };
            let runs = features.map_or(0, |f| {
                f.data
                    .records
                    .iter()
                    .enumerate()
                    .filter(|(i, r)| *i == 0 || f.data.records[i - 1].positive != r.positive)
                    .count()
            });
            let (rings, collapsed) = boolean::snap(&rings, 10f64.powf(digits as f64))?;
            Ok((NativeRegion::from(rings), runs, collapsed))
        })
        .map_err(value_error)
    }
    #[pyo3(signature=(features,profile,invert,seed,*,variables=None))]
    fn drawing(
        &mut self,
        py: Python<'_>,
        features: Option<&NativeFeatures>,
        profile: Option<&NativeRegion>,
        invert: bool,
        seed: bool,
        variables: Option<HashMap<String, String>>,
    ) -> PyResult<NativeDrawing> {
        py.detach(|| {
            self.engine.set_label_variables(variables.unwrap_or_default());
            self.engine.drawing(
                features.map(|f| f.data.as_ref()),
                profile.map(|p| p.data.as_slice()),
                invert,
                seed,
            )
        })
        .map(|data| NativeDrawing { data })
        .map_err(value_error)
    }
    fn diagnostics_json(&self) -> String {
        serde_json::to_string(&self.engine.diagnostics).unwrap()
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeRing>()?;
    m.add_class::<NativeRegion>()?;
    m.add_class::<NativeFeatures>()?;
    m.add_class::<NativeProfile>()?;
    m.add_class::<NativeFont>()?;
    m.add_class::<Composer>()?;
    m.add_class::<NativeDrawing>()?;
    m.add_function(wrap_pyfunction!(region_boolean, m)?)?;
    m.add_function(wrap_pyfunction!(describe_symbol, m)?)?;
    m.add_function(wrap_pyfunction!(symbol_cycles, m)?)?;
    Ok(())
}
