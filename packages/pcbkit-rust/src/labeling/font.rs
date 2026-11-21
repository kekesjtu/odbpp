use super::{bounds, shift};
use crate::{
    geometry::{Point, Ring, boolean, curves},
    parser::Result,
};
use std::{
    collections::{BTreeSet, HashMap, hash_map::Entry},
    f64::consts::TAU,
};

#[derive(Clone, Copy)]
struct Line {
    start: Point,
    end: Point,
    width: f64,
    round: bool,
    positive: bool,
}

pub struct Font {
    pitch: f64,
    height: f64,
    stroke: f64,
    glyphs: HashMap<char, Vec<Line>>,
    reference: Option<char>,
}

fn number(raw: &str) -> Result<f64> {
    raw.parse::<f64>()
        .ok()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("invalid font dimension or coordinate {raw:?}"))
}

impl Font {
    pub fn parse(source: &str, path: &str) -> Result<Self> {
        let mut font = Self {
            pitch: 0.,
            height: 0.,
            stroke: 0.,
            glyphs: HashMap::new(),
            reference: None,
        };
        let mut spacing = 0.;
        let mut glyph = None;
        for (i, raw) in source.lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with("UNITS=") {
                continue;
            }
            let result: Result<()> = (|| {
                if let Some(character) = line.strip_prefix("CHAR") {
                    let mut chars = character.trim().chars();
                    let ch = chars
                        .next()
                        .filter(char::is_ascii)
                        .ok_or("CHAR needs one ASCII character")?;
                    if chars.next().is_some() || glyph.is_some() || font.glyphs.contains_key(&ch) {
                        return Err("invalid, nested or duplicate CHAR".into());
                    }
                    glyph = Some((ch, Vec::new()));
                    return Ok(());
                }
                let fields: Vec<_> = line.split_whitespace().collect();
                match fields.as_slice() {
                    ["XSIZE", n] if glyph.is_none() => font.pitch = number(n)?,
                    ["YSIZE", n] if glyph.is_none() => font.height = number(n)?,
                    ["OFFSET", n] if glyph.is_none() => spacing = number(n)?,
                    ["LINE", xs, ys, xe, ye, polarity, shape, width] => {
                        let width = number(width)?;
                        if width < 0. {
                            return Err("font stroke width cannot be negative".into());
                        }
                        let positive = match *polarity {
                            "P" => true,
                            "N" => false,
                            _ => return Err("font stroke polarity must be P or N".into()),
                        };
                        let round = match *shape {
                            "R" => true,
                            "S" => false,
                            _ => return Err("font stroke ends must be R or S".into()),
                        };
                        let (_, lines) = glyph.as_mut().ok_or("LINE outside of a CHAR block")?;
                        lines.push(Line {
                            start: (number(xs)?, number(ys)?),
                            end: (number(xe)?, number(ye)?),
                            width,
                            round,
                            positive,
                        });
                        if positive {
                            font.stroke = font.stroke.max(width);
                        }
                    }
                    ["ECHAR"] => {
                        let (ch, lines) = glyph.take().ok_or("ECHAR without a CHAR block")?;
                        font.glyphs.insert(ch, lines);
                    }
                    _ => return Err(format!("invalid font record {line:?}")),
                }
                Ok(())
            })();
            result.map_err(|err| format!("{path}:{}: {err}", i + 1))?;
        }
        if glyph.is_some() {
            return Err(format!("{path}: missing ECHAR at end of font"));
        }
        if font.pitch <= 0. || font.height <= 0. || font.pitch + spacing <= 0. {
            return Err(format!("{path}: invalid font size or offset"));
        }
        font.pitch += spacing;
        font.reference = ('A'..='Z')
            .filter_map(|ch| {
                let lines = font.glyphs.get(&ch)?;
                let (left, right) = lines.iter().filter(|line| line.positive).fold(
                    (f64::INFINITY, f64::NEG_INFINITY),
                    |(left, right), line| {
                        (
                            left.min(line.start.0.min(line.end.0)),
                            right.max(line.start.0.max(line.end.0)),
                        )
                    },
                );
                Some((ch, right - left))
            })
            .max_by(|a, b| a.1.total_cmp(&b.1).then((a.0 == 'M').cmp(&(b.0 == 'M'))))
            .map(|(ch, _)| ch);
        Ok(font)
    }

    pub(super) fn render(
        &self,
        value: &str,
        size: Point,
        stroke: f64,
        centred: bool,
        sag: f64,
        max: usize,
    ) -> Result<(Vec<Ring>, BTreeSet<char>)> {
        let mut missing = BTreeSet::new();
        let mut rings = Vec::new();
        if stroke == 0. || self.stroke == 0. {
            return Ok((rings, missing));
        }
        let scale = (size.0 / self.pitch, size.1 / self.height);
        let render = |lines: &[Line]| self.glyph(lines, scale, stroke / self.stroke, sag, max);
        let mut glyphs = HashMap::new();
        let mut leading_space = 0.;
        for (i, ch) in value.chars().enumerate() {
            if ch == ' ' {
                if rings.is_empty() {
                    leading_space += size.0;
                }
                continue;
            }
            let Some(lines) = self.glyphs.get(&ch) else {
                missing.insert(ch);
                continue;
            };
            let glyph = match glyphs.entry(ch) {
                Entry::Occupied(entry) => entry.into_mut(),
                Entry::Vacant(entry) => entry.insert(render(lines)?),
            };
            let mut placed = glyph.clone();
            let centre = if centred {
                bounds(glyph).map_or(0., |(x0, _, x1, _)| (x0 + x1) / 2.)
            } else {
                0.
            };
            shift(&mut placed, (i as f64 * size.0 - centre, 0.));
            rings.extend(placed);
        }
        if let Some((x0, y0, _, _)) = bounds(&rings) {
            let anchor = if centred {
                let reference = self
                    .reference
                    .map(|ch| render(&self.glyphs[&ch]))
                    .transpose()?;
                reference
                    .as_deref()
                    .and_then(bounds)
                    .map_or((-size.0 / 2., y0), |(left, bottom, right, _)| {
                        ((left - right) / 2., bottom)
                    })
            } else {
                (x0 - leading_space, y0)
            };
            shift(&mut rings, (-anchor.0, -anchor.1));
        }
        Ok((rings, missing))
    }

    fn glyph(
        &self,
        lines: &[Line],
        (sx, sy): Point,
        stroke: f64,
        sag: f64,
        max: usize,
    ) -> Result<Vec<Ring>> {
        let mut rings = Vec::new();
        for line in lines {
            if line.width == 0. {
                continue;
            }
            let start = (line.start.0 * sx, line.start.1 * sy);
            let end = (line.end.0 * sx, line.end.1 * sy);
            let radius = line.width * stroke / 2.;
            let part = if line.round {
                if start == end {
                    vec![(
                        curves::arc_vertices(start, radius, 0., -TAU, sag, max)?,
                        false,
                    )]
                } else {
                    boolean::stroke(&[vec![start, end]], radius, sag)?
                }
            } else {
                let (dx, dy) = (end.0 - start.0, end.1 - start.1);
                let (sin, cos) = dy.atan2(dx).sin_cos();
                let (tx, ty) = (radius * cos, radius * sin);
                vec![(
                    vec![
                        (start.0 - tx - ty, start.1 - ty + tx),
                        (end.0 + tx - ty, end.1 + ty + tx),
                        (end.0 + tx + ty, end.1 + ty - tx),
                        (start.0 - tx + ty, start.1 - ty - tx),
                    ],
                    false,
                )]
            };
            if line.positive {
                rings.extend(part);
            } else {
                rings = boolean::operate("difference", &rings, &part, boolean::SCALE)?;
            }
        }
        boolean::operate("union", &rings, &[], boolean::SCALE)
    }
}
