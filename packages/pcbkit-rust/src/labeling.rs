use crate::{
    geometry::{Point, Ring},
    parser::{Diagnostic, Placement, Result, warn},
};
use std::{collections::HashMap, sync::Arc};

mod barcode;
mod font;

pub use font::Font;

#[derive(Clone, Copy, Debug)]
pub enum LabelKind {
    Text {
        stroke: f64,
        centred: bool,
    },
    Barcode {
        full_ascii: bool,
        checksum: bool,
        background: bool,
        caption_top: Option<bool>,
    },
}
impl LabelKind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Text { .. } => "text",
            Self::Barcode { .. } => "barcode",
        }
    }

    pub fn uses_font(self) -> bool {
        !matches!(
            self,
            Self::Barcode {
                caption_top: None,
                ..
            }
        )
    }
}

#[derive(Clone, Debug)]
pub struct Label {
    pub placement: Placement,
    pub size: Point,
    pub font: String,
    pub value: String,
    pub kind: LabelKind,
}

#[derive(Default)]
pub struct Labels {
    pub fonts: HashMap<String, Arc<Font>>,
    pub variables: HashMap<String, String>,
}
impl Labels {
    pub fn region(
        &self,
        label: &Label,
        sagitta: f64,
        maximum: usize,
        diagnostics: &mut Vec<Diagnostic>,
        path: &str,
    ) -> Result<Vec<Ring>> {
        let value = self.expand(label, diagnostics, path);
        let font = self.fonts.get(&label.font);
        if font.is_none() && label.kind.uses_font() {
            warn(
                diagnostics,
                "geometry.font_missing",
                format!("no ODB++ font definition for {:?}", label.font),
                path,
            );
        }
        let mut missing = Vec::new();
        let mut text = |size, stroke, centred, value: &str| -> Result<Vec<Ring>> {
            font.map_or(Ok(vec![]), |f| {
                let (rings, undefined) =
                    f.render(value, size, stroke, centred, sagitta, maximum)?;
                missing.extend(undefined);
                Ok(rings)
            })
        };
        let mut rings = match label.kind {
            LabelKind::Text { stroke, centred } => text(label.size, stroke, centred, &value)?,
            LabelKind::Barcode {
                full_ascii,
                checksum,
                background,
                caption_top,
            } => {
                let (mut rings, width) =
                    barcode::region(&value, label.size, full_ascii, checksum, background)?;
                if let Some(top) = caption_top {
                    let (element, height) = label.size;
                    let size = height / 3.;
                    let mut caption = text((size, size), element.min(size / 4.), false, &value)?;
                    if let Some((x0, y0, x1, y1)) = bounds(&caption) {
                        let y = if top {
                            height * 7. / 6. - y0
                        } else {
                            -height / 6. - y1
                        };
                        shift(&mut caption, ((width - x0 - x1) / 2., y));
                    }
                    rings.extend(caption);
                }
                rings
            }
        };
        for character in missing {
            warn(
                diagnostics,
                "geometry.glyph_missing",
                format!("font {:?} has no glyph for {character:?}", label.font),
                path,
            );
        }
        label.placement.transform(&mut rings);
        Ok(rings)
    }

    fn expand(&self, label: &Label, diagnostics: &mut Vec<Diagnostic>, path: &str) -> String {
        let (x, y) = label.placement.origin;
        let mut tail = label.value.as_str();
        let mut value = String::with_capacity(tail.len());
        while let Some((before, after)) = tail.split_once("$$") {
            value.push_str(before);
            let word_len = |s: &str| {
                s.bytes()
                    .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
                    .count()
            };
            let end = word_len(after);
            let first = &after[..end];
            let key = if matches!(first, "DATE" | "WEEK") && after[end..].starts_with('-') {
                let end = end + 1 + word_len(&after[end + 1..]);
                &after[..end]
            } else {
                first
            };
            let coordinate = match key {
                "X" => Some(x / 25.4),
                "Y" => Some(y / 25.4),
                "X_MM" => Some(x),
                "Y_MM" => Some(y),
                _ => None,
            };
            if let Some(coordinate) = coordinate {
                value.push_str(&coordinate.to_string());
            } else if let Some(replacement) = self.variables.get(key) {
                value.push_str(replacement);
            } else {
                value.push_str("$$");
                value.push_str(key);
                if !key.is_empty() {
                    warn(
                        diagnostics,
                        "geometry.text_variable_undefined",
                        format!("unresolved ODB++ text variable {}{key}", "$$"),
                        path,
                    );
                }
            }
            tail = &after[key.len()..];
        }
        value.push_str(tail);
        value
    }
}

fn bounds(rings: &[Ring]) -> Option<(f64, f64, f64, f64)> {
    rings
        .iter()
        .flat_map(|(points, _)| points)
        .fold(None, |bounds, &(x, y)| {
            Some(match bounds {
                None => (x, y, x, y),
                Some((x0, y0, x1, y1)) => (x0.min(x), y0.min(y), x1.max(x), y1.max(y)),
            })
        })
}

fn shift(rings: &mut [Ring], (dx, dy): Point) {
    for (points, _) in rings {
        for (x, y) in points {
            *x += dx;
            *y += dy;
        }
    }
}
