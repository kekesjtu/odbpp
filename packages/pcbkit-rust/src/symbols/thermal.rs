use super::{Standard, circle, oval, rect};
use crate::{
    geometry::{Point, Ring, boolean},
    parser::Result,
};
use std::f64::consts::{SQRT_2, TAU};

pub(super) fn region(symbol: &Standard, sag: f64, max: usize) -> Result<Vec<Ring>> {
    let family = symbol.prefix;
    let round = matches!(family, "thr" | "ths");
    let oblong = matches!(family, "o_ths" | "oblong_ths");
    let rectangular = matches!(family, "rc_ths" | "rc_tho");
    let w = symbol.size(0);
    let h = if oblong || rectangular {
        symbol.size(1)
    } else {
        w
    };
    let thickness = if oblong || rectangular {
        symbol.size(5)
    } else {
        (w - symbol.size(1)) / 2.
    };
    let gap = symbol.size(4);
    let spokes = symbol.count(3, max)?;
    if w <= 0. || h <= 0. || thickness <= 0. || 2. * thickness > w.min(h) || gap < 0. {
        return Err("invalid thermal dimensions".into());
    }
    let angle = symbol.number(2).rem_euclid(360.).to_radians();
    let angles = (0..spokes).map(|i| angle + TAU * i as f64 / spokes as f64);
    let radius = symbol.unit.size(symbol.radius);
    let chamfer = symbol.unit.size(symbol.chamfer);
    let inner_w = w - 2. * thickness;
    let inner_h = h - 2. * thickness;
    let outline = if round {
        circle(w, sag, max)?
    } else if oblong {
        oval(w, h, false, sag, max)?
    } else {
        rect(w, h, radius, chamfer, &symbol.corners, sag, max)?
    };
    let opening = if round || family == "sr_ths" {
        circle(inner_w, sag, max)?
    } else if oblong {
        oval(inner_w, inner_h, false, sag, max)?
    } else {
        rect(
            inner_w,
            inner_h,
            (radius - thickness).max(0.),
            (chamfer - (2. - SQRT_2) * thickness).max(0.),
            &symbol.corners,
            sag,
            max,
        )?
    };
    let outer = [(outline, false)];
    let inner = (opening, false);
    if gap == 0. && family != "s_thr" {
        return boolean::operate("difference", &outer, &[inner], boolean::SCALE);
    }

    let rounded_ends =
        matches!(family, "thr" | "s_thr") || (family == "oblong_ths" && symbol.round_ends);
    let cut_width = if rounded_ends { gap + thickness } else { gap };
    let mid = ((w - thickness) / 2., (h - thickness) / 2.);
    let reach = w.hypot(h) + cut_width;
    let open_corners = matches!(family, "s_tho" | "rc_tho");
    let cutters: Vec<Ring> = angles
        .map(|angle| {
            let (dy, dx) = angle.sin_cos();
            let diagonal = (dx.abs() - dy.abs()).abs() < 1e-10;
            let points = if diagonal && open_corners {
                corner_cut(
                    w / 2.,
                    h / 2.,
                    thickness + gap / SQRT_2,
                    dx.signum(),
                    dy.signum(),
                )
            } else {
                let offset = if diagonal && rectangular {
                    mid.1 * dy.signum() * dx - mid.0 * dx.signum() * dy
                } else {
                    0.
                };
                gap_ray((dx, dy), offset, cut_width, reach)
            };
            (points, false)
        })
        .collect();
    if !rounded_ends {
        let mut clearances = cutters;
        clearances.push(inner);
        return boolean::operate("difference", &outer, &clearances, boolean::SCALE);
    }

    let mut lines = Vec::new();
    if family == "s_thr" {
        let (x, y) = mid;
        let corners = [(-x, -y), (x, -y), (x, y), (-x, y)];
        for i in 0..4 {
            lines.push(vec![corners[i], corners[(i + 1) % 4]]);
        }
    } else {
        let mut track = oval(w - thickness, h - thickness, false, sag, max)?;
        track.push(track[0]);
        let middle = track.len() / 2;
        lines.push(track[..=middle].to_vec());
        lines.push(track[middle..].to_vec());
    }
    let paths = boolean::cut_lines(&lines, &cutters)?;
    let rounded = boolean::stroke(&paths, thickness / 2., sag)?;
    let band = boolean::operate("difference", &outer, &[inner], boolean::SCALE)?;
    boolean::operate("intersection", &band, &rounded, boolean::SCALE)
}

fn gap_ray((dx, dy): Point, offset: f64, width: f64, reach: f64) -> Vec<Point> {
    let near = offset - width / 2.;
    let far = offset + width / 2.;
    [(0., near), (reach, near), (reach, far), (0., far)]
        .into_iter()
        .map(|(u, v)| (u * dx - v * dy, u * dy + v * dx))
        .collect()
}

fn corner_cut(x: f64, y: f64, trim: f64, sx: f64, sy: f64) -> Vec<Point> {
    [
        (x - trim, y - trim),
        (2. * x, y - trim),
        (2. * x, 2. * y),
        (x - trim, 2. * y),
    ]
    .into_iter()
    .map(|(x, y)| (x * sx, y * sy))
    .collect()
}
