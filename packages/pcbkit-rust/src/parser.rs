//! ODB++ line records. Coordinates, attributes and curves stay native.
use crate::geometry::{Point, Ring, boolean, curves};
use serde::Serialize;
use serde_json::{Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    sync::Arc,
};

pub type Result<T> = std::result::Result<T, String>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Unit {
    Mm,
    Inch,
}
impl Unit {
    pub fn parse(s: &str) -> Result<Self> {
        match s.to_ascii_uppercase().as_str() {
            "MM" | "M" => Ok(Self::Mm),
            "INCH" | "I" => Ok(Self::Inch),
            _ => Err(format!("unknown units {s:?}")),
        }
    }
    pub fn name(self) -> &'static str {
        if self == Self::Mm { "MM" } else { "INCH" }
    }
    pub fn coord(self, n: f64) -> f64 {
        n * if self == Self::Mm { 1. } else { 25.4 }
    }
    pub fn size(self, n: f64) -> f64 {
        n * if self == Self::Mm { 0.001 } else { 0.0254 }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Diagnostic {
    pub level: &'static str,
    pub code: &'static str,
    pub message: String,
    pub path: String,
}
pub fn warn(diags: &mut Vec<Diagnostic>, code: &'static str, message: String, path: &str) {
    if !diags
        .iter()
        .any(|d| d.code == code && d.message == message && d.path == path)
    {
        diags.push(Diagnostic {
            level: "warning",
            code,
            message,
            path: path.into(),
        });
    }
}

#[derive(Clone, Debug)]
pub struct SymbolRef {
    pub index: usize,
    pub name: String,
    pub unit: Unit,
}
#[derive(Clone, Debug)]
pub struct Edge {
    pub end: Point,
    pub arc: Option<(Point, bool)>,
}
#[derive(Clone, Debug)]
pub struct Contour {
    pub start: Point,
    pub hole: bool,
    pub edges: Vec<Edge>,
}
#[derive(Clone, Debug, Default)]
pub struct Surface {
    pub contours: Vec<Contour>,
}
impl Surface {
    pub fn region(&self, sagitta: f64, maximum: usize) -> Result<Vec<Ring>> {
        let mut rings = Vec::with_capacity(self.contours.len());
        for contour in &self.contours {
            let mut points = vec![contour.start];
            let mut current = contour.start;
            for edge in &contour.edges {
                if let Some((center, clockwise)) = edge.arc {
                    points.extend(curves::arc_points(
                        current, edge.end, center, clockwise, sagitta, maximum,
                    )?);
                } else {
                    points.push(edge.end);
                }
                current = edge.end;
            }
            rings.push((points, contour.hole));
        }
        // Normalisation also resolves self contacts before the region is used.
        boolean::operate("union", &rings, &[], boolean::SCALE)
    }
}

#[derive(Clone, Debug)]
pub enum Shape {
    Pad {
        origin: Point,
        symbol: Option<SymbolRef>,
        resize: f64,
        angle: f64,
        mirrored: bool,
    },
    Line {
        start: Point,
        end: Point,
        symbol: Option<SymbolRef>,
    },
    Arc {
        start: Point,
        end: Point,
        center: Point,
        clockwise: bool,
        symbol: Option<SymbolRef>,
    },
    Surface(Surface),
}
impl Shape {
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Pad { .. } => "pad",
            Self::Line { .. } => "line",
            Self::Arc { .. } => "arc",
            Self::Surface(_) => "surface",
        }
    }
    pub fn symbol(&self) -> Option<&SymbolRef> {
        match self {
            Self::Pad { symbol, .. } | Self::Line { symbol, .. } | Self::Arc { symbol, .. } => {
                symbol.as_ref()
            }
            _ => None,
        }
    }
}
#[derive(Clone, Debug)]
pub struct Record {
    pub positive: bool,
    pub shape: Shape,
    pub line: usize,
    pub raw: String,
    pub uid: Option<i64>,
    pub dcode: i64,
    pub attributes: Map<String, Value>,
}
#[derive(Clone, Debug)]
pub struct Features {
    pub path: String,
    pub unit: Unit,
    pub declared_count: Option<usize>,
    pub symbols: Vec<SymbolRef>,
    pub records: Vec<Record>,
    pub diagnostics: Vec<Diagnostic>,
}
impl Features {
    pub fn profile(&self) -> Result<Surface> {
        let mut surface = Surface::default();
        for record in &self.records {
            if let Shape::Surface(s) = &record.shape {
                surface.contours.extend(s.contours.iter().cloned());
            }
        }
        if surface.contours.is_empty() {
            return Err(format!(
                "{}: profile contains no surface feature",
                self.path
            ));
        }
        Ok(surface)
    }
    pub fn custom_references(&self) -> Vec<String> {
        let mut names = BTreeSet::new();
        for symbol in &self.symbols {
            if crate::symbols::parse(&symbol.name, symbol.unit).is_none() {
                names.insert(symbol.name.clone());
            }
        }
        names.into_iter().collect()
    }
    pub fn histogram(&self) -> Value {
        let mut counts = BTreeMap::new();
        for r in &self.records {
            let s = r.shape.symbol();
            *counts
                .entry((
                    r.shape.kind(),
                    if r.positive { "P" } else { "N" },
                    s.map_or("", |s| s.name.as_str()),
                    s.map_or("", |s| s.unit.name()),
                ))
                .or_insert(0usize) += 1;
        }
        Value::Array(counts.into_iter().map(|((kind,polarity,symbol,units),count)| serde_json::json!({"kind":kind,"polarity":polarity,"symbol":symbol,"symbol_units":units,"count":count})).collect())
    }
}

fn tokens(s: &str) -> Result<Vec<&str>> {
    let mut result = vec![];
    let mut tail = s.trim();
    while !tail.is_empty() {
        if let Some(rest) = tail.strip_prefix('\'') {
            let end = rest.find('\'').ok_or("unterminated quoted string")?;
            result.push(&rest[..end]);
            tail = &rest[end + 1..];
            if !tail.is_empty() && !tail.starts_with(char::is_whitespace) {
                return Err("quoted string must be followed by whitespace".into());
            }
        } else {
            let end = tail.find(char::is_whitespace).unwrap_or(tail.len());
            result.push(&tail[..end]);
            tail = &tail[end..];
        }
        tail = tail.trim_start();
    }
    Ok(result)
}
fn sections(s: &str) -> Result<Vec<&str>> {
    let mut result = vec![];
    let mut start = 0;
    let mut quoted = false;
    for (i, c) in s.char_indices() {
        if c == '\'' {
            quoted = !quoted;
        } else if c == ';' && !quoted {
            result.push(&s[start..i]);
            start = i + 1;
        }
    }
    if quoted {
        return Err("unterminated quoted string".into());
    }
    result.push(&s[start..]);
    Ok(result)
}
fn num(t: &[&str], i: usize) -> Result<f64> {
    let raw = t
        .get(i)
        .ok_or_else(|| format!("missing numeric field {i}"))?;
    let value: f64 = raw
        .parse()
        .map_err(|_| format!("expected a number, got {raw:?}"))?;
    if !value.is_finite() {
        return Err("non-finite numeric field".into());
    }
    Ok(value)
}
fn integer(t: &[&str], i: usize) -> Result<i64> {
    let raw = t
        .get(i)
        .ok_or_else(|| format!("missing integer field {i}"))?;
    raw.parse().or_else(|_| num(t, i).map(|n| n as i64))
}
fn polarity(t: &[&str], i: usize) -> Result<bool> {
    match t.get(i).copied() {
        Some("P" | "p") => Ok(true),
        Some("N" | "n") => Ok(false),
        _ => Err("expected polarity P or N".into()),
    }
}
fn point(t: &[&str], i: usize, unit: Unit) -> Result<Point> {
    Ok((unit.coord(num(t, i)?), unit.coord(num(t, i + 1)?)))
}

pub fn parse(
    text: &str,
    path: &str,
    default_unit: Unit,
    definitions: &HashMap<String, String>,
    require_units: bool,
) -> Result<Arc<Features>> {
    let mut file = Features {
        path: path.into(),
        unit: default_unit,
        declared_count: None,
        symbols: vec![],
        records: vec![],
        diagnostics: vec![],
    };
    let mut symbol_table = HashMap::new();
    let mut attr_names = HashMap::new();
    let mut attr_strings = HashMap::new();
    let mut surface: Option<usize> = None;
    let mut contour: Option<Contour> = None;
    let mut declared_units = false;
    for (index, raw) in text.lines().enumerate() {
        let line = raw.trim();
        let line_no = index + 1;
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parsed: Result<()> = (|| {
            if let Some(u) = line.strip_prefix("UNITS=") {
                file.unit = Unit::parse(u.trim())?;
                declared_units = true;
                return Ok(());
            }
            if let Some(id) = line.strip_prefix("ID=") {
                id.trim().parse::<i64>().map_err(|_| "malformed file ID")?;
                return Ok(());
            }
            if line.starts_with(['$', '@', '&']) {
                let t = tokens(line)?;
                let id: usize = t[0][1..].parse().map_err(|_| "invalid table index")?;
                match line.as_bytes()[0] {
                    b'$' => {
                        let name = t.get(1).ok_or("symbol line without a name")?.to_string();
                        let unit = t
                            .get(2)
                            .filter(|s| matches!(**s, "I" | "M" | "i" | "m"))
                            .map_or(Ok(file.unit), |u| Unit::parse(u))?;
                        let symbol = SymbolRef {
                            index: id,
                            name,
                            unit,
                        };
                        symbol_table.insert(id, symbol.clone());
                        file.symbols.push(symbol);
                    }
                    b'@' => {
                        attr_names.insert(id, t.get(1).copied().unwrap_or("").to_string());
                    }
                    _ => {
                        attr_strings
                            .insert(id, line[t[0].len()..].trim().trim_matches('\'').to_string());
                    }
                }
                return Ok(());
            }
            let secs = sections(line)?;
            let t = tokens(secs[0])?;
            let kind = t.first().copied().ok_or("empty record")?;
            match kind {
                "F" => {
                    file.declared_count = Some(
                        integer(&t, 1)?
                            .try_into()
                            .map_err(|_| "negative feature count")?,
                    );
                    return Ok(());
                }
                "OB" => {
                    if surface.is_none() {
                        return Err("OB outside of a surface".into());
                    }
                    if contour.is_some() {
                        return Err("nested OB: previous polygon was never closed".into());
                    }
                    let hole = match t.get(3).copied() {
                        Some("I" | "i") => false,
                        Some("H" | "h") => true,
                        _ => return Err("OB polygon type must be I or H".into()),
                    };
                    contour = Some(Contour {
                        start: point(&t, 1, file.unit)?,
                        hole,
                        edges: vec![],
                    });
                    return Ok(());
                }
                "OS" | "OC" => {
                    let c = contour
                        .as_mut()
                        .ok_or("contour edge outside of a polygon")?;
                    let arc = if kind == "OC" {
                        if t.len() < 6 {
                            return Err("OC needs xe ye xc yc cw".into());
                        }
                        Some((
                            point(&t, 3, file.unit)?,
                            !t[5..].iter().any(|s| s.eq_ignore_ascii_case("N")),
                        ))
                    } else {
                        None
                    };
                    c.edges.push(Edge {
                        end: point(&t, 1, file.unit)?,
                        arc,
                    });
                    return Ok(());
                }
                "OE" => {
                    let c = contour.take().ok_or("OE without a matching OB")?;
                    let i = surface.ok_or("OE outside of a surface")?;
                    if let Shape::Surface(s) = &mut file.records[i].shape {
                        s.contours.push(c);
                    }
                    return Ok(());
                }
                "SE" => {
                    if contour.is_some() {
                        return Err("surface closed while a polygon was still open".into());
                    }
                    surface = None;
                    return Ok(());
                }
                "P" | "L" | "A" | "S" | "T" | "B" => {}
                _ => {
                    warn(
                        &mut file.diagnostics,
                        "features.unknown_record",
                        format!("unrecognised record {kind:?}; ignored"),
                        path,
                    );
                    return Ok(());
                }
            }
            if matches!(kind, "T" | "B") {
                warn(
                    &mut file.diagnostics,
                    "features.unsupported_record",
                    format!("{kind} records are outside this parser's scope; ignored"),
                    path,
                );
                return Ok(());
            }
            if surface.is_some() {
                return Err("feature record inside an unterminated surface".into());
            }
            let mut attrs = Map::new();
            let mut uid = None;
            for sec in &secs[1..] {
                for item in sec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
                    let (key, value) = item
                        .split_once('=')
                        .map_or((item, None), |(k, v)| (k.trim(), Some(v.trim())));
                    if key.eq_ignore_ascii_case("ID") {
                        uid = Some(
                            value
                                .ok_or("missing ID value")?
                                .parse()
                                .map_err(|_| "malformed feature ID")?,
                        );
                        continue;
                    }
                    let id: usize = key
                        .parse()
                        .map_err(|_| "attribute index is not an integer")?;
                    let Some(name) = attr_names.get(&id) else {
                        warn(
                            &mut file.diagnostics,
                            "features.bad_attribute_ref",
                            format!("attribute index {id} is outside the attribute name table"),
                            path,
                        );
                        continue;
                    };
                    let kind = definitions.get(name).map_or("", String::as_str);
                    let value = match value {
                        None => Value::Bool(true),
                        Some(_) if kind == "BOOLEAN" => Value::Bool(true),
                        Some(raw) if kind == "TEXT" => {
                            match raw.parse::<usize>().ok().and_then(|n| attr_strings.get(&n)) {
                                Some(s) => Value::String(s.clone()),
                                None => {
                                    warn(
                                        &mut file.diagnostics,
                                        "features.bad_attribute_ref",
                                        format!(
                                            "attribute {name:?} references unavailable text string {raw}"
                                        ),
                                        path,
                                    );
                                    Value::String(raw.into())
                                }
                            }
                        }
                        Some(raw) if matches!(kind, "INTEGER" | "OPTION") => raw
                            .parse::<i64>()
                            .map(Value::from)
                            .unwrap_or_else(|_| Value::String(raw.into())),
                        Some(raw) if matches!(kind, "FLOAT" | "REAL") => raw
                            .parse::<f64>()
                            .ok()
                            .filter(|v| v.is_finite())
                            .map(Value::from)
                            .unwrap_or_else(|| Value::String(raw.into())),
                        Some(raw) => Value::String(raw.into()),
                    };
                    if kind.is_empty() {
                        warn(
                            &mut file.diagnostics,
                            "features.undeclared_attribute",
                            format!("attribute {name:?} has no definition and keeps its raw token"),
                            path,
                        );
                    }
                    attrs.insert(name.clone(), value);
                }
            }
            let symbol = |i: usize| -> Result<Option<SymbolRef>> {
                Ok(usize::try_from(integer(&t, i)?)
                    .ok()
                    .and_then(|i| symbol_table.get(&i).cloned()))
            };
            let (shape, positive, dcode) = match kind {
                "P" => {
                    let mut pos = 3;
                    let mut si = integer(&t, pos)?;
                    pos += 1;
                    let mut resize = 0.;
                    if si == -1 {
                        si = integer(&t, pos)?;
                        resize = file.unit.size(num(&t, pos + 1)?);
                        pos += 2;
                    }
                    let positive = polarity(&t, pos)?;
                    pos += 1;
                    let dcode = integer(&t, pos)?;
                    pos += 1;
                    let code = integer(&t, pos)?;
                    let (angle, mirrored) = match code {
                        0..=7 => ((code % 4) as f64 * 90., code >= 4),
                        8 | 9 => (num(&t, pos + 1)?.rem_euclid(360.), code == 9),
                        _ => return Err("invalid orientation definition (expected 0-9)".into()),
                    };
                    (
                        Shape::Pad {
                            origin: point(&t, 1, file.unit)?,
                            symbol: usize::try_from(si)
                                .ok()
                                .and_then(|i| symbol_table.get(&i).cloned()),
                            resize,
                            angle,
                            mirrored,
                        },
                        positive,
                        dcode,
                    )
                }
                "L" => (
                    Shape::Line {
                        start: point(&t, 1, file.unit)?,
                        end: point(&t, 3, file.unit)?,
                        symbol: symbol(5)?,
                    },
                    polarity(&t, 6)?,
                    if t.len() > 7 { integer(&t, 7)? } else { 0 },
                ),
                "A" => {
                    let mut clockwise = true;
                    let mut dcode = 0;
                    for value in &t[9.min(t.len())..] {
                        match *value {
                            "Y" | "y" => clockwise = true,
                            "N" | "n" => clockwise = false,
                            _ => {
                                if let Ok(n) = value.parse::<f64>() {
                                    dcode = n as i64;
                                }
                            }
                        }
                    }
                    (
                        Shape::Arc {
                            start: point(&t, 1, file.unit)?,
                            end: point(&t, 3, file.unit)?,
                            center: point(&t, 5, file.unit)?,
                            clockwise,
                            symbol: symbol(7)?,
                        },
                        polarity(&t, 8)?,
                        dcode,
                    )
                }
                _ => (
                    Shape::Surface(Surface::default()),
                    polarity(&t, 1)?,
                    if t.len() > 2 { integer(&t, 2)? } else { 0 },
                ),
            };
            if kind == "S" {
                surface = Some(file.records.len());
            }
            file.records.push(Record {
                positive,
                shape,
                line: line_no,
                raw: line.into(),
                uid,
                dcode,
                attributes: attrs,
            });
            Ok(())
        })();
        parsed.map_err(|e| format!("{path}:{line_no}: {e}"))?;
    }
    if surface.is_some() || contour.is_some() {
        return Err(format!("{path}: file ended inside an unterminated surface"));
    }
    if !declared_units && require_units {
        warn(
            &mut file.diagnostics,
            "features.no_units",
            "no UNITS directive; falling back to the product model default".into(),
            path,
        );
    }
    if file.declared_count.is_some_and(|n| n != file.records.len()) {
        warn(
            &mut file.diagnostics,
            "features.count_mismatch",
            format!(
                "declared {} features but parsed {}",
                file.declared_count.unwrap(),
                file.records.len()
            ),
            path,
        );
    }
    Ok(Arc::new(file))
}
