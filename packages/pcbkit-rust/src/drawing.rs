//! Ordered paint operations for preview-only layers; no whole-layer boolean.
use pcb_geometry::{Point, Ring};
use std::fmt::Write;

pub struct Paint {
    pub positive: bool,
    pub width: f64,
    pub points: Vec<Point>,
    pub rings: Vec<Ring>,
}

pub struct Drawing {
    pub operations: Vec<Paint>,
    pub bounds: Option<(f64, f64, f64, f64)>,
}
impl Drawing {
    pub fn new(operations: Vec<Paint>) -> Self {
        let mut bounds: Option<(f64, f64, f64, f64)> = None;
        for op in &operations {
            let margin = op.width / 2.;
            for &(x, y) in op
                .points
                .iter()
                .chain(op.rings.iter().flat_map(|r| r.0.iter()))
            {
                bounds = Some(match bounds {
                    None => (x - margin, y - margin, x + margin, y + margin),
                    Some((a, b, c, d)) => (
                        a.min(x - margin),
                        b.min(y - margin),
                        c.max(x + margin),
                        d.max(y + margin),
                    ),
                });
            }
        }
        Self { operations, bounds }
    }

    pub fn svg(&self) -> String {
        fn path(data: &mut String, points: &[Point], close: bool) {
            if let Some((x, y)) = points.first() {
                write!(data, "M{x} {y}").unwrap();
                for (x, y) in &points[1..] {
                    write!(data, "L{x} {y}").unwrap();
                }
                if close {
                    data.push('Z');
                }
            }
        }
        fn flush(out: &mut String, key: Option<(bool, f64)>, data: &mut String) {
            if let Some((positive, width)) = key {
                let colour = if positive { "white" } else { "black" };
                if width > 0. {
                    write!(out,"<path fill=\"none\" stroke=\"{colour}\" stroke-width=\"{width}\" stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"{data}\"/>").unwrap();
                } else {
                    write!(
                        out,
                        "<path fill=\"{colour}\" fill-rule=\"nonzero\" d=\"{data}\"/>"
                    )
                    .unwrap();
                }
                data.clear();
            }
        }
        let mut out = String::new();
        let mut data = String::new();
        let mut key = None;
        for op in &self.operations {
            let next = Some((op.positive, op.width));
            if next != key {
                flush(&mut out, key, &mut data);
                key = next;
            }
            if op.width > 0. {
                path(&mut data, &op.points, false);
            } else {
                for (points, _) in &op.rings {
                    path(&mut data, points, true);
                }
            }
        }
        flush(&mut out, key, &mut data);
        out
    }
}
