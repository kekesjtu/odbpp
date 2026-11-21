//! Full-name standard-symbol recognition and native construction.
use crate::{
    geometry::{Point, Ring, boolean, curves},
    parser::{Result, Unit},
};
use std::f64::consts::{FRAC_PI_2, PI, TAU};

mod stencil;
mod thermal;

struct Spec {
    prefix: &'static str,
    names: &'static [&'static str],
    min: usize,
    rounding: bool,
    corners: bool,
    style: bool,
    plating: bool,
}
macro_rules! spec {
    ($p:literal, [$($n:literal),*], $min:literal, $r:literal, $c:literal, $s:literal, $pl:literal) => {Spec{prefix:$p,names:&[$($n),*],min:$min,rounding:$r,corners:$c,style:$s,plating:$pl}};
}
static SPECS: &[Spec] = &[
    spec!("r", ["d"], 1, false, false, false, false),
    spec!("s", ["s"], 1, false, false, false, false),
    spec!("rect", ["w", "h"], 2, true, true, false, false),
    spec!("di", ["w", "h"], 2, false, false, false, false),
    spec!("oct", ["w", "h", "r"], 2, false, false, false, false),
    spec!("el", ["w", "h"], 2, false, false, false, false),
    spec!("tri", ["base", "h"], 2, false, false, false, false),
    spec!("oval", ["w", "h"], 2, false, false, false, false),
    spec!("oval_h", ["w", "h"], 2, false, false, false, false),
    spec!(
        "moire",
        ["rw", "rg", "nr", "lw", "ll", "la"],
        6,
        false,
        false,
        false,
        false
    ),
    spec!("ext", ["n"], 0, false, false, false, false),
    spec!("donut_r", ["od", "id"], 2, false, false, false, false),
    spec!("donut_s", ["od", "id"], 2, true, true, false, false),
    spec!("donut_sr", ["od", "id"], 2, false, false, false, false),
    spec!("donut_rc", ["ow", "oh", "lw"], 3, true, true, false, false),
    spec!("donut_o", ["ow", "oh", "lw"], 3, false, false, false, false),
    spec!("hex_l", ["w", "h", "r"], 2, false, false, false, false),
    spec!("hex_s", ["w", "h", "r"], 2, false, false, false, false),
    spec!("bfr", ["d"], 1, false, false, false, false),
    spec!("bfs", ["s"], 1, false, false, false, false),
    spec!(
        "thr",
        ["od", "id", "angle", "num_spokes", "gap"],
        5,
        false,
        false,
        false,
        false
    ),
    spec!(
        "ths",
        ["od", "id", "angle", "num_spokes", "gap"],
        5,
        false,
        false,
        false,
        false
    ),
    spec!(
        "s_ths",
        ["os", "is", "angle", "num_spokes", "gap"],
        5,
        true,
        true,
        false,
        false
    ),
    spec!(
        "s_tho",
        ["od", "id", "angle", "num_spokes", "gap"],
        5,
        false,
        false,
        false,
        false
    ),
    spec!(
        "s_thr",
        ["os", "is", "angle", "num_spokes", "gap"],
        5,
        false,
        false,
        false,
        false
    ),
    spec!(
        "sr_ths",
        ["os", "id", "angle", "num_spokes", "gap"],
        5,
        false,
        false,
        false,
        false
    ),
    spec!(
        "rc_ths",
        ["ow", "oh", "angle", "num_spokes", "gap", "air_gap"],
        6,
        true,
        true,
        false,
        false
    ),
    spec!(
        "rc_tho",
        ["w", "h", "angle", "num_spokes", "gap", "air_gap"],
        6,
        false,
        false,
        false,
        false
    ),
    spec!(
        "o_ths",
        ["ow", "oh", "angle", "num_spokes", "gap", "lw"],
        6,
        false,
        false,
        false,
        false
    ),
    spec!(
        "oblong_ths",
        ["ow", "oh", "angle", "num_spokes", "gap", "lw"],
        6,
        false,
        false,
        true,
        false
    ),
    spec!(
        "hplate",
        ["w", "h", "c", "ra", "ro"],
        3,
        false,
        false,
        false,
        false
    ),
    spec!(
        "rhplate",
        ["w", "h", "c", "ra", "ro"],
        3,
        false,
        false,
        false,
        false
    ),
    spec!(
        "fhplate",
        ["w", "h", "vc", "hc", "ra", "ro"],
        4,
        false,
        false,
        false,
        false
    ),
    spec!(
        "radhplate",
        ["w", "h", "ms", "ra"],
        3,
        false,
        false,
        false,
        false
    ),
    spec!(
        "dshape",
        ["w", "h", "r", "ra"],
        3,
        false,
        false,
        false,
        false
    ),
    spec!(
        "cross",
        ["w", "h", "hs", "vs", "hc", "vc", "ra"],
        6,
        false,
        false,
        true,
        false
    ),
    spec!(
        "dogbone",
        ["w", "h", "hs", "vs", "hc", "ra"],
        5,
        false,
        false,
        true,
        false
    ),
    spec!(
        "dpack",
        ["w", "h", "hg", "vg", "hn", "vn", "ra"],
        6,
        false,
        false,
        false,
        false
    ),
    spec!("hole", ["d", "tp", "tm"], 3, false, false, false, true),
];

#[derive(Clone, Debug)]
pub struct Standard {
    pub prefix: &'static str,
    pub names: &'static [&'static str],
    pub numbers: Vec<f64>,
    pub radius: f64,
    pub chamfer: f64,
    pub corners: Vec<u32>,
    pub unit: Unit,
    round_ends: bool,
}
fn decimal(s: &str) -> Option<f64> {
    if s.is_empty()
        || s.chars()
            .any(|c| !c.is_ascii_digit() && !matches!(c, '.' | '+' | '-'))
    {
        return None;
    }
    s.parse::<f64>().ok().filter(|v| v.is_finite())
}
pub fn parse(name: &str, mut unit: Unit) -> Option<Standard> {
    let mut words: Vec<_> = name.split_whitespace().collect();
    while words.len() > 1 && matches!(words.last().copied(), Some("I" | "M" | "i" | "m")) {
        unit = Unit::parse(words.pop()?).ok()?;
    }
    if words.len() != 1 {
        return None;
    }
    let name = words[0];
    for spec in SPECS {
        let Some(rest) = name.strip_prefix(spec.prefix) else {
            continue;
        };
        let mut parts: Vec<_> = if rest.is_empty() && spec.prefix == "ext" {
            vec![]
        } else {
            rest.split('x').collect()
        };
        if spec.plating {
            if parts.len() < 2 || !matches!(parts[1], "p" | "n" | "v" | "P" | "N" | "V") {
                continue;
            }
            parts.remove(1);
        }
        let mut i = 0;
        let mut numbers = vec![];
        while i < parts.len() && numbers.len() < spec.names.len() {
            if let Some(n) = decimal(parts[i]) {
                numbers.push(n);
                i += 1;
            } else {
                break;
            }
        }
        let mut radius = 0.;
        let mut chamfer = 0.;
        let mut rounded = false;
        let mut corners = vec![];
        if i < parts.len() && spec.rounding {
            if let Some(n) = parts[i]
                .strip_prefix('r')
                .and_then(decimal)
                .filter(|n| *n >= 0.)
            {
                radius = n;
                rounded = true;
                i += 1;
            } else if let Some(n) = parts[i]
                .strip_prefix('c')
                .and_then(decimal)
                .filter(|n| *n >= 0.)
            {
                chamfer = n;
                rounded = true;
                i += 1;
            }
        }
        if i < parts.len()
            && rounded
            && spec.corners
            && !parts[i].is_empty()
            && parts[i].bytes().all(|c| c.is_ascii_digit())
        {
            corners = parts[i].chars().filter_map(|c| c.to_digit(10)).collect();
            i += 1;
        }
        let mut round_ends = false;
        if i < parts.len() && spec.style {
            if let Some(style @ ('r' | 's' | 'R' | 'S')) = parts[i].chars().next() {
                let length = &parts[i][1..];
                round_ends = matches!(style, 'r' | 'R');
                if !length.is_empty() && numbers.len() < spec.names.len() {
                    let Some(value) = decimal(length.strip_prefix("ra").unwrap_or(length)) else {
                        continue;
                    };
                    numbers.push(value);
                } else if !length.is_empty() {
                    continue;
                }
                i += 1;
            }
        }
        for key in ["ra", "ro"] {
            if i < parts.len()
                && let Some(slot) = spec.names.iter().position(|name| *name == key)
                && let Some(value) = parts[i].strip_prefix(key).and_then(decimal)
                && numbers.len() <= slot
            {
                numbers.resize(slot, 0.);
                numbers.push(value);
                i += 1;
            }
        }
        if spec.style
            && i < parts.len()
            && numbers.len() + 1 == spec.names.len()
            && spec.names.last() == Some(&"ra")
            && let Some(value) = decimal(parts[i])
        {
            numbers.push(value);
            i += 1;
        }
        if i != parts.len()
            || numbers.len() < spec.min
            || numbers.len() > spec.names.len()
            || corners.len() > 4
        {
            continue;
        }
        return Some(Standard {
            prefix: spec.prefix,
            names: spec.names,
            numbers,
            radius,
            chamfer,
            corners,
            unit,
            round_ends,
        });
    }
    None
}
fn arc(center: Point, r: f64, start: f64, sweep: f64, sag: f64, max: usize) -> Result<Vec<Point>> {
    curves::arc_vertices(center, r, start, sweep, sag, max)
}
pub fn square(side: f64) -> Vec<Point> {
    let h = side / 2.;
    if side <= 0. {
        vec![]
    } else {
        vec![(h, h), (h, -h), (-h, -h), (-h, h)]
    }
}
fn rect(
    w: f64,
    h: f64,
    r: f64,
    ch: f64,
    corners: &[u32],
    sag: f64,
    max: usize,
) -> Result<Vec<Point>> {
    if w <= 0. || h <= 0. {
        return Ok(vec![]);
    }
    if corners.iter().any(|n| !(1..=4).contains(n)) {
        return Err("corner mask names corners outside 1..4".into());
    }
    let (w, h) = (w / 2., h / 2.);
    let mut p = vec![];
    for (n, sx, sy, a) in [
        (1, 1., 1., FRAC_PI_2),
        (4, 1., -1., 0.),
        (3, -1., -1., -FRAC_PI_2),
        (2, -1., 1., PI),
    ] {
        let (x, y) = (sx * w, sy * h);
        if !corners.is_empty() && !corners.contains(&n) {
            p.push((x, y));
            continue;
        }
        if r > 0. {
            let cut = r.min(w.min(h));
            p.extend(arc(
                (x - sx * cut, y - sy * cut),
                cut,
                a,
                -FRAC_PI_2,
                sag,
                max,
            )?);
        } else if ch > 0. {
            let cut = ch.min(w.min(h));
            if n == 1 || n == 3 {
                p.extend([(x - sx * cut, y), (x, y - sy * cut)]);
            } else {
                p.extend([(x, y - sy * cut), (x - sx * cut, y)]);
            }
        } else {
            p.push((x, y));
        }
    }
    Ok(p)
}
fn oval(w: f64, h: f64, half: bool, sag: f64, max: usize) -> Result<Vec<Point>> {
    if w <= 0. || h <= 0. {
        return Ok(vec![]);
    }
    let mut p;
    if half {
        if w >= h {
            let (r, s) = (h / 2., (w - h) / 2.);
            p = arc((s, 0.), r, FRAC_PI_2, -FRAC_PI_2, sag, max)?;
            p.extend(arc((-s, 0.), r, PI, -FRAC_PI_2, sag, max)?);
            p.push((s, r));
        } else {
            let (r, s) = (w / 2., (h - w) / 2.);
            p = arc((0., s), r, 0., PI, sag, max)?;
            p.extend([(-r, 0.), (r, 0.), (r, s)]);
        }
    } else if w >= h {
        let (r, s) = (h / 2., (w - h) / 2.);
        p = arc((s, 0.), r, FRAC_PI_2, -PI, sag, max)?;
        p.push((-s, -r));
        p.extend(arc((-s, 0.), r, -FRAC_PI_2, -PI, sag, max)?);
        p.push((s, r));
    } else {
        let (r, s) = (w / 2., (h - w) / 2.);
        p = vec![(r, -s), (r, s)];
        p.extend(arc((0., s), r, 0., PI, sag, max)?);
        p.push((-r, -s));
        p.extend(arc((0., -s), r, PI, PI, sag, max)?);
    }
    Ok(p)
}
impl Standard {
    fn number(&self, i: usize) -> f64 {
        self.numbers.get(i).copied().unwrap_or(0.)
    }

    pub fn size(&self, i: usize) -> f64 {
        self.unit.size(self.number(i))
    }

    fn count(&self, i: usize, maximum: usize) -> Result<usize> {
        let value = self.number(i);
        if value.fract() != 0. || value < 1. || value > maximum as f64 {
            return Err(format!(
                "{} must be an integer in 1..={maximum}",
                self.names[i]
            ));
        }
        Ok(value as usize)
    }

    pub fn region(&self, sag: f64, max: usize) -> Result<Vec<Ring>> {
        let (w, h) = (self.size(0), self.size(1));
        let (rx, ry) = (w / 2., h / 2.);
        let (r, ch) = (self.unit.size(self.radius), self.unit.size(self.chamfer));
        let circle = |d: f64| circle(d, sag, max);
        let mut rings = vec![];
        let p = match self.prefix {
            "r" | "hole" => circle(w)?,
            "ext" => return Ok(vec![]),
            "s" => square(w),
            "rect" => rect(w, h, r, ch, &self.corners, sag, max)?,
            "oval" | "oval_h" => oval(w, h, self.prefix == "oval_h", sag, max)?,
            "di" => vec![(0., ry), (rx, 0.), (0., -ry), (-rx, 0.)],
            "el" => ellipse(w, h, sag, max)?,
            "tri" => vec![(0., ry), (rx, -ry), (-rx, -ry)],
            "hex_l" => vec![
                (rx, 0.),
                (rx / 2., ry),
                (-rx / 2., ry),
                (-rx, 0.),
                (-rx / 2., -ry),
                (rx / 2., -ry),
            ],
            "hex_s" => vec![
                (0., ry),
                (rx, ry / 2.),
                (rx, -ry / 2.),
                (0., -ry),
                (-rx, -ry / 2.),
                (-rx, ry / 2.),
            ],
            "oct" => {
                let c = self.size(2);
                let k = 2. - 2f64.sqrt();
                let (cx, cy) = if c > 0. {
                    (c.min(rx), c.min(ry))
                } else {
                    (rx * k, ry * k)
                };
                vec![
                    (rx, ry - cy),
                    (rx - cx, ry),
                    (-rx + cx, ry),
                    (-rx, ry - cy),
                    (-rx, -ry + cy),
                    (-rx + cx, -ry),
                    (rx - cx, -ry),
                    (rx, -ry + cy),
                ]
            }
            "donut_r" => {
                if w > 0. && h > 0. {
                    let inner: Vec<Point> = circle(w)?
                        .into_iter()
                        .map(|(x, y)| (x * h / w, y * h / w))
                        .collect();
                    rings.push((inner, true));
                }
                circle(w)?
            }
            "donut_s" => {
                rings.push((square(h), true));
                square(w)
            }
            "donut_sr" => {
                return boolean::operate(
                    "difference",
                    &[(square(w), false)],
                    &[(circle(h)?, false)],
                    boolean::SCALE,
                );
            }
            "donut_rc" => {
                let lw = self.size(2);
                rings.push((rect(w - 2. * lw, h - 2. * lw, 0., 0., &[], sag, max)?, true));
                rect(w, h, r, ch, &self.corners, sag, max)?
            }
            "donut_o" => {
                let lw = self.size(2);
                rings.push((oval(w - 2. * lw, h - 2. * lw, false, sag, max)?, true));
                oval(w, h, false, sag, max)?
            }
            "bfr" | "bfs" => return butterfly(w, self.prefix == "bfr", sag, max),
            "moire" => return self.moire(sag, max),
            "thr" | "ths" | "s_ths" | "s_tho" | "s_thr" | "sr_ths" | "rc_ths" | "rc_tho"
            | "o_ths" | "oblong_ths" => return thermal::region(self, sag, max),
            "hplate" | "rhplate" | "fhplate" | "radhplate" | "dshape" | "cross" | "dogbone"
            | "dpack" => return stencil::region(self, sag, max),
            _ => {
                unreachable!("standard symbol grammar and geometry must agree");
            }
        };
        if w > 0. && (matches!(self.prefix, "r" | "hole" | "s" | "donut_r" | "donut_s") || h > 0.) {
            rings.insert(0, (p, false));
        }
        boolean::operate("union", &rings, &[], boolean::SCALE)
    }

    fn moire(&self, sag: f64, max: usize) -> Result<Vec<Ring>> {
        let (rw, rg, lw, ll) = (self.size(0), self.size(1), self.size(3), self.size(4));
        let count = self.count(2, max)?;
        if rw <= 0. || rg < 0. || lw < 0. || ll < 0. {
            return Err("invalid moire ring or line dimensions".into());
        }
        let pitch = rg + rw / 2.;
        let mut rings = Vec::new();
        for i in 1..=count {
            let diameter = 2. * pitch * i as f64;
            let outer = circle(diameter + rw, sag, max)?;
            if diameter > rw {
                let ratio = (diameter - rw) / (diameter + rw);
                let inner = outer.iter().map(|&(x, y)| (x * ratio, y * ratio)).collect();
                rings.push((inner, true));
            }
            rings.push((outer, false));
        }
        let line_angle = self.number(5).to_radians();
        for angle in [line_angle, line_angle + FRAC_PI_2] {
            let (sin, cos) = angle.sin_cos();
            let line = rect(ll, lw, 0., 0., &[], sag, max)?
                .into_iter()
                .map(|(x, y)| (x * cos - y * sin, x * sin + y * cos))
                .collect();
            rings.push((line, false));
        }
        boolean::operate("union", &rings, &[], boolean::SCALE)
    }
}

fn circle(diameter: f64, sag: f64, max: usize) -> Result<Vec<Point>> {
    arc((0., 0.), diameter / 2., 0., -TAU, sag, max)
}

fn ellipse(w: f64, h: f64, sag: f64, max: usize) -> Result<Vec<Point>> {
    if w <= 0. || h <= 0. {
        return Ok(vec![]);
    }
    let diameter = w.max(h);
    // Split at the axes so each extremum survives tessellation.
    let mut points = Vec::new();
    for quadrant in 0..4 {
        points.extend(
            arc(
                (0., 0.),
                diameter / 2.,
                -(quadrant as f64) * FRAC_PI_2,
                -FRAC_PI_2,
                sag,
                max,
            )?
            .into_iter()
            .skip(1)
            .map(|(x, y)| (x * w / diameter, y * h / diameter)),
        );
    }
    Ok(points)
}

fn butterfly(size: f64, round: bool, sag: f64, max: usize) -> Result<Vec<Ring>> {
    if size <= 0. {
        return Ok(vec![]);
    }
    let half = size / 2.;
    let mut upper_left = vec![(0., 0.)];
    if round {
        upper_left.extend(arc((0., 0.), half, FRAC_PI_2, FRAC_PI_2, sag, max)?);
    } else {
        upper_left.extend([(0., half), (-half, half), (-half, 0.)]);
    }
    let lower_right = upper_left.iter().map(|&(x, y)| (-x, -y)).collect();
    Ok(vec![(upper_left, false), (lower_right, false)])
}
