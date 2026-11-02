//! Active-edge rasterization. Positive nested rings use even/odd per region;
//! independently supplied drill regions are combined with OR.
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};
type Edge = (f64, f64, f64, f64);
type Span = (f64, f64);
pub type Axis = (f64, f64, usize);
pub const MAX_RASTER_PIXELS: usize = 120_000_000;

struct Scan {
    edges: Vec<(f64, f64, Edge)>,
    active: Vec<(f64, f64, Edge)>,
    cursor: usize,
}
impl Scan {
    fn new(edges: Vec<Edge>) -> Result<Self, String> {
        if edges
            .iter()
            .any(|e| !e.0.is_finite() || !e.1.is_finite() || !e.2.is_finite() || !e.3.is_finite())
        {
            return Err("non-finite raster edge".into());
        }
        let mut edges: Vec<_> = edges
            .into_iter()
            .filter(|e| e.1 != e.3)
            .map(|e| (e.1.min(e.3), e.1.max(e.3), e))
            .collect();
        edges.sort_by(|a, b| a.0.total_cmp(&b.0));
        Ok(Self {
            edges,
            active: vec![],
            cursor: 0,
        })
    }
    fn spans(&mut self, y: f64) -> Vec<Span> {
        while self.cursor < self.edges.len() && self.edges[self.cursor].0 <= y {
            self.active.push(self.edges[self.cursor]);
            self.cursor += 1;
        }
        self.active.retain(|e| e.1 > y);
        let mut xs: Vec<f64> = self
            .active
            .iter()
            .map(|&(_, _, (x1, y1, x2, y2))| {
                let t = (y - y1) / (y2 - y1);
                x1 + (x2 - x1) * t
            })
            .collect();
        xs.sort_by(f64::total_cmp);
        xs.as_chunks::<2>()
            .0
            .iter()
            .filter(|p| p[1] > p[0])
            .map(|p| (p[0], p[1]))
            .collect()
    }
    fn window(&self, low: f64, high: f64) -> Self {
        let stop = self.edges.partition_point(|e| e.0 <= high);
        Self {
            edges: self.edges[..stop]
                .iter()
                .filter(|e| e.1 > low)
                .copied()
                .collect(),
            active: vec![],
            cursor: 0,
        }
    }
}

fn intersection(a: &[Span], b: &[Span]) -> Vec<Span> {
    let (mut i, mut j) = (0, 0);
    let mut out = vec![];
    while i < a.len() && j < b.len() {
        let (lo, hi) = (a[i].0.max(b[j].0), a[i].1.min(b[j].1));
        if hi > lo + 1e-9 {
            out.push((lo, hi));
        }
        if a[i].1 < b[j].1 {
            i += 1;
        } else {
            j += 1;
        }
    }
    out
}

pub fn pixel_index(value: f64, axis: Axis) -> usize {
    (((value - axis.0) / axis.1 - 0.5 - 1e-9).ceil().max(0.) as usize).min(axis.2)
}

#[pyfunction]
pub fn rasterize<'py>(
    py: Python<'py>,
    domain: Vec<Edge>,
    step: Option<Vec<Edge>>,
    black: Vec<Vec<Edge>>,
    x: Axis,
    y: Axis,
) -> PyResult<Bound<'py, PyBytes>> {
    let pixels = py
        .detach(|| rasterize_pixels(domain, step, black, x, y))
        .map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &pixels))
}

pub fn rasterize_pixels(
    domain: Vec<Edge>,
    step: Option<Vec<Edge>>,
    black: Vec<Vec<Edge>>,
    x: Axis,
    y: Axis,
) -> Result<Vec<u8>, String> {
    PreparedRaster::new(domain, step, black)?.render(x, y)
}

pub struct PreparedRaster {
    domain: Scan,
    step: Option<Scan>,
    black: Vec<Scan>,
}
impl PreparedRaster {
    pub fn new(
        domain: Vec<Edge>,
        step: Option<Vec<Edge>>,
        black: Vec<Vec<Edge>>,
    ) -> Result<Self, String> {
        Ok(Self {
            domain: Scan::new(domain)?,
            step: step.map(Scan::new).transpose()?,
            black: black.into_iter().map(Scan::new).collect::<Result<_, _>>()?,
        })
    }
    pub fn render(&self, x: Axis, y: Axis) -> Result<Vec<u8>, String> {
        for a in [x, y] {
            if !a.0.is_finite() || !a.1.is_finite() || a.1 <= 0. || a.2 == 0 {
                return Err("invalid raster axis".into());
            }
        }
        let count =
            x.2.checked_mul(y.2)
                .filter(|n| *n <= MAX_RASTER_PIXELS)
                .ok_or("raster resource limit: 120000000 pixels")?;
        let (low, high) = (y.0 + 0.5 * y.1, y.0 + (y.2 as f64 - 0.5) * y.1);
        let mut domain = self.domain.window(low, high);
        let mut step = self.step.as_ref().map(|s| s.window(low, high));
        let mut black: Vec<_> = self.black.iter().map(|s| s.window(low, high)).collect();
        let pixels = {
            let mut pixels = vec![160u8; count];
            for row in 0..y.2 {
                let yy = y.0 + (row as f64 + 0.5) * y.1;
                let mut dom = domain.spans(yy);
                if let Some(scan) = &mut step {
                    dom = intersection(&dom, &scan.spans(yy));
                }
                let mut ink: Vec<Span> = black.iter_mut().flat_map(|scan| scan.spans(yy)).collect();
                ink.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
                let mut merged: Vec<Span> = vec![];
                for (a, b) in ink {
                    if let Some(last) = merged.last_mut()
                        && a <= last.1
                    {
                        last.1 = last.1.max(b);
                        continue;
                    }
                    merged.push((a, b));
                }
                for (spans, value) in [(dom.clone(), 255u8), (intersection(&dom, &merged), 0u8)] {
                    for (lo, hi) in spans {
                        let (a, b) = (pixel_index(lo, x), pixel_index(hi, x));
                        if b > a {
                            pixels[row * x.2 + a..row * x.2 + b].fill(value);
                        }
                    }
                }
            }
            pixels
        };
        Ok(pixels)
    }
}
