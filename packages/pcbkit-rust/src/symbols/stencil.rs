use super::{Standard, arc, rect};
use crate::{
    geometry::{Point, Ring, boolean},
    parser::Result,
};
use std::f64::consts::{FRAC_PI_2, PI};

pub(super) fn region(symbol: &Standard, sag: f64, max: usize) -> Result<Vec<Ring>> {
    let (w, h) = (symbol.size(0), symbol.size(1));
    if w <= 0. || h <= 0. {
        return Err("stencil width and height must be positive".into());
    }
    let points = match symbol.prefix {
        "cross" | "dogbone" => return cross_or_dogbone(symbol, w, h, sag, max),
        "dpack" => return pack(symbol, w, h, sag, max),
        "radhplate" => radiused_inverted(w, h, symbol.size(2), symbol.size(3), sag, max)?,
        "dshape" => radiused_plate(w, h, symbol.size(2), symbol.size(3), sag, max)?,
        _ => plate(symbol, w, h, sag, max)?,
    };
    Ok(vec![(points, false)])
}

fn plate(symbol: &Standard, w: f64, h: f64, sag: f64, max: usize) -> Result<Vec<Point>> {
    let (x, y) = (w / 2., h / 2.);
    let flat = symbol.prefix == "fhplate";
    let (acute, obtuse) = if flat {
        (symbol.size(4), symbol.size(5))
    } else {
        (symbol.size(3), symbol.size(4))
    };
    let cut = symbol.size(2);
    let mut corners = vec![((-x, -y), acute), ((-x, y), acute)];
    if flat {
        let hc = symbol.size(3);
        if !(0. ..=y).contains(&cut) || !(0. ..w).contains(&hc) {
            return Err("invalid flat home plate cuts".into());
        }
        corners.extend([
            ((x - hc, y), obtuse),
            ((x, y - cut), acute),
            ((x, -y + cut), acute),
            ((x - hc, -y), obtuse),
        ]);
    } else if symbol.prefix == "rhplate" {
        if !(0. ..w).contains(&cut) {
            return Err("invalid inverted home plate cut".into());
        }
        corners.extend([((x, y), acute), ((x - cut, 0.), obtuse), ((x, -y), acute)]);
    } else {
        if !(0. ..=w).contains(&cut) {
            return Err("invalid home plate cut".into());
        }
        corners.extend([
            ((x - cut, y), obtuse),
            ((x, 0.), acute),
            ((x - cut, -y), obtuse),
        ]);
    }
    fillet(corners, false, sag, max)
}

fn radiused_inverted(
    w: f64,
    h: f64,
    middle: f64,
    radius: f64,
    sag: f64,
    max: usize,
) -> Result<Vec<Point>> {
    let shoulder = (h - middle) / 4.;
    let join = w / 2. - shoulder;
    let (x, y) = (w / 2., h / 2.);
    if !(0. ..w.min(h)).contains(&middle) || radius < 0. || radius > y.min(w - shoulder) {
        return Err("invalid radiused inverted home plate dimensions".into());
    }
    let mut points = arc((join, y - shoulder), shoulder, FRAC_PI_2, -PI, sag, max)?;
    points.extend(arc((join, 0.), middle / 2., FRAC_PI_2, PI, sag, max)?);
    points.extend(arc(
        (join, -y + shoulder),
        shoulder,
        FRAC_PI_2,
        -PI,
        sag,
        max,
    )?);
    close_left(&mut points, x, y, radius, sag, max)?;
    Ok(points)
}

fn radiused_plate(
    w: f64,
    h: f64,
    relief: f64,
    radius: f64,
    sag: f64,
    max: usize,
) -> Result<Vec<Point>> {
    let (x, y) = (w / 2., h / 2.);
    if !(0. ..=w).contains(&relief) || radius < 0. || radius > x.min(y) {
        return Err("invalid radiused home plate dimensions".into());
    }
    if relief == 0. {
        return rect(w, h, radius, 0., &[], sag, max);
    }
    let mut points = Vec::new();
    if relief >= y {
        let cap = arc((0., 0.), relief, FRAC_PI_2, -PI, sag, max)?;
        points.extend(
            cap.into_iter()
                .map(|(u, v)| (x - relief + u, v * y / relief)),
        );
    } else {
        let outer_radius = (y * y / relief + relief) / 2.;
        let centre_x = x - outer_radius;
        let tangent_y = y - radius;
        let tangent_x = ((outer_radius - radius).powi(2) - tangent_y.powi(2))
            .max(0.)
            .sqrt();
        let angle = tangent_y.atan2(tangent_x);
        let fillet_x = centre_x + tangent_x;
        if radius > 0. {
            points.extend(arc(
                (fillet_x, tangent_y),
                radius,
                FRAC_PI_2,
                angle - FRAC_PI_2,
                sag,
                max,
            )?);
        }
        points.extend(arc(
            (centre_x, 0.),
            outer_radius,
            angle,
            -2. * angle,
            sag,
            max,
        )?);
        if radius > 0. {
            points.extend(arc(
                (fillet_x, -tangent_y),
                radius,
                -angle,
                angle - FRAC_PI_2,
                sag,
                max,
            )?);
        }
    }
    if points[0].0 < -x + radius {
        return Err("radiused home plate corners overlap".into());
    }
    close_left(&mut points, x, y, radius, sag, max)?;
    Ok(points)
}

fn close_left(points: &mut Vec<Point>, x: f64, y: f64, r: f64, sag: f64, max: usize) -> Result<()> {
    if r == 0. {
        points.extend([(-x, -y), (-x, y)]);
    } else {
        points.extend(arc((-x + r, -y + r), r, -FRAC_PI_2, -FRAC_PI_2, sag, max)?);
        points.extend(arc((-x + r, y - r), r, PI, -FRAC_PI_2, sag, max)?);
    }
    Ok(())
}

fn cross_or_dogbone(symbol: &Standard, w: f64, h: f64, sag: f64, max: usize) -> Result<Vec<Ring>> {
    let dogbone = symbol.prefix == "dogbone";
    let (hs, vs) = (symbol.size(2), symbol.size(3));
    if hs <= 0. || vs <= 0. || hs >= h || vs >= w || (dogbone && 2. * hs >= h) {
        return Err("invalid stencil line widths".into());
    }
    let hc = symbol.number(4);
    let vc = if dogbone { 50. } else { symbol.number(5) };
    let ra = symbol.size(if dogbone { 5 } else { 6 });
    if !(0. ..=100.).contains(&hc) || !(0. ..=100.).contains(&vc) || ra < 0. {
        return Err("invalid stencil cross point or radius".into());
    }
    let cross_x = (w * (hc / 100. - 0.5)).clamp(-(w - vs) / 2., (w - vs) / 2.);
    let cross_y = (h * (vc / 100. - 0.5)).clamp(-(h - hs) / 2., (h - hs) / 2.);
    let bar = |width, height, round: f64, offset, ends: [bool; 2], vertical| -> Result<Ring> {
        let mut corners = Vec::new();
        if ends[0] {
            corners.extend(if vertical { [3, 4] } else { [2, 3] });
        }
        if ends[1] {
            corners.extend(if vertical { [1, 2] } else { [1, 4] });
        }
        let rounded = if corners.is_empty() { 0. } else { round };
        let mut points = rect(width, height, rounded, 0., &corners, sag, max)?;
        shift(&mut points, offset);
        Ok((points, false))
    };
    let horizontal_ends = [
        symbol.round_ends && cross_x > -(w - hs) / 2.,
        symbol.round_ends && cross_x < (w - hs) / 2.,
    ];
    let mut bars = Vec::new();
    if dogbone {
        for y in [-(h - hs) / 2., (h - hs) / 2.] {
            bars.push(bar(w, hs, hs / 2., (0., y), horizontal_ends, false)?);
        }
        bars.push(bar(vs, h - hs, 0., (cross_x, 0.), [false; 2], true)?);
    } else {
        let vertical_ends = [
            symbol.round_ends && cross_y > -(h - vs) / 2.,
            symbol.round_ends && cross_y < (h - vs) / 2.,
        ];
        bars.push(bar(w, hs, hs / 2., (0., cross_y), horizontal_ends, false)?);
        bars.push(bar(vs, h, vs / 2., (cross_x, 0.), vertical_ends, true)?);
    }
    let mut rings = boolean::operate("union", &bars, &[], boolean::SCALE)?;
    if ra > 0. {
        for (points, _) in &mut rings {
            let corners = (0..points.len())
                .map(|i| {
                    let next = points[(i + 1) % points.len()];
                    let prev = points[(i + points.len() - 1) % points.len()];
                    let turn = bend(prev, points[i], next).0;
                    let inside = turn > 0.;
                    let square_tip = dogbone && !symbol.round_ends && turn < 0.;
                    let radius = if inside || square_tip { ra } else { 0. };
                    (points[i], radius)
                })
                .collect();
            *points = fillet(corners, true, sag, max)?;
        }
    }
    Ok(rings)
}

fn pack(symbol: &Standard, w: f64, h: f64, sag: f64, max: usize) -> Result<Vec<Ring>> {
    let (nx, ny) = (symbol.count(4, max)?, symbol.count(5, max)?);
    if nx * ny > max {
        return Err("D-pack pad count exceeds the expansion limit".into());
    }
    let (gx, gy, ra) = (symbol.size(2), symbol.size(3), symbol.size(6));
    let pw = (w - gx * (nx - 1) as f64) / nx as f64;
    let ph = (h - gy * (ny - 1) as f64) / ny as f64;
    if gx < 0. || gy < 0. || pw <= 0. || ph <= 0. || !(0. ..=pw.min(ph) / 2.).contains(&ra) {
        return Err("invalid D-pack spacing or corner radius".into());
    }
    let pad = rect(pw, ph, ra, 0., &[], sag, max)?;
    let mut rings = Vec::with_capacity(nx * ny);
    for iy in 0..ny {
        let y = -h / 2. + ph / 2. + iy as f64 * (ph + gy);
        for ix in 0..nx {
            let x = -w / 2. + pw / 2. + ix as f64 * (pw + gx);
            let mut points = pad.clone();
            shift(&mut points, (x, y));
            rings.push((points, false));
        }
    }
    boolean::operate("union", &rings, &[], boolean::SCALE)
}

fn shift(points: &mut [Point], (dx, dy): Point) {
    for (x, y) in points {
        *x += dx;
        *y += dy;
    }
}

fn bend(prev: Point, (x, y): Point, next: Point) -> (f64, f64, f64) {
    let incoming = (x - prev.0, y - prev.1);
    let outgoing = (next.0 - x, next.1 - y);
    let cross = incoming.0 * outgoing.1 - incoming.1 * outgoing.0;
    let dot = incoming.0 * outgoing.0 + incoming.1 * outgoing.1;
    (
        cross.atan2(dot),
        incoming.0.hypot(incoming.1),
        outgoing.0.hypot(outgoing.1),
    )
}

fn fillet(mut corners: Vec<(Point, f64)>, limit: bool, sag: f64, max: usize) -> Result<Vec<Point>> {
    corners.dedup_by(|after, before| {
        if after.0 == before.0 {
            before.1 = before.1.max(after.1);
            true
        } else {
            false
        }
    });
    let count = corners.len();
    let mut bends = Vec::with_capacity(count);
    for i in 0..count {
        let (point, radius) = corners[i];
        if radius < 0. {
            return Err("corner radius cannot be negative".into());
        }
        let (turn, before, after) = bend(
            corners[(i + count - 1) % count].0,
            point,
            corners[(i + 1) % count].0,
        );
        let ratio = (turn.abs() / 2.).tan();
        let mut tangent = radius * ratio;
        if limit {
            tangent = tangent.min(before.min(after) / 2.);
        }
        bends.push((turn, after, tangent));
    }
    let mut points = Vec::new();
    for i in 0..count {
        let (turn, length, tangent) = bends[i];
        let prev = (i + count - 1) % count;
        if tangent + bends[(i + 1) % count].2 > length + 1e-12 {
            return Err("stencil corner rounds overlap".into());
        }
        let (x, y) = corners[i].0;
        if tangent <= 1e-12 {
            points.push((x, y));
            continue;
        }
        let (px, py) = corners[prev].0;
        let direction = ((y - py).atan2(x - px), turn.signum());
        let (sin, cos) = direction.0.sin_cos();
        let radius = tangent / (turn.abs() / 2.).tan();
        let centre = (
            x - tangent * cos - direction.1 * radius * sin,
            y - tangent * sin + direction.1 * radius * cos,
        );
        points.extend(arc(
            centre,
            radius,
            direction.0 - direction.1 * FRAC_PI_2,
            turn,
            sag,
            max,
        )?);
    }
    Ok(points)
}
