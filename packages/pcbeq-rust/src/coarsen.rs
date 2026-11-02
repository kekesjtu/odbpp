//! Adjacent true-interval coarsening with continuous geometric acceptance.
use crate::{
    geos::{Result, Shape},
    topology::Source,
};
use std::collections::{HashMap, HashSet};
pub type Rect = [f64; 4];
#[derive(Clone, Debug)]
pub struct Band {
    pub a: f64,
    pub b: f64,
    pub spans: Vec<(f64, f64)>,
    pub depth: usize,
    pub loss: f64,
}
pub fn area(r: Rect) -> f64 {
    (r[2] - r[0]).max(0.) * (r[3] - r[1]).max(0.)
}
pub fn pack(bands: &[Band]) -> Vec<Rect> {
    let mut rects: Vec<Rect> = vec![];
    let mut active: HashMap<(u64, u64), usize> = HashMap::new();
    for band in bands {
        let mut next = HashMap::new();
        for &(low, high) in &band.spans {
            let key = (low.to_bits(), high.to_bits());
            let i = if let Some(&i) = active.get(&key) {
                if rects[i][2] == band.a {
                    rects[i][2] = band.b;
                    i
                } else {
                    rects.push([band.a, low, band.b, high]);
                    rects.len() - 1
                }
            } else {
                rects.push([band.a, low, band.b, high]);
                rects.len() - 1
            };
            next.insert(key, i);
        }
        active = next;
    }
    rects
}
pub fn unique(values: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut v: Vec<f64> = values.collect();
    v.sort_by(f64::total_cmp);
    v.dedup();
    v
}
pub fn score(rects: &[Rect]) -> (usize, usize) {
    (
        unique(rects.iter().flat_map(|r| [r[0], r[2]])).len()
            + unique(rects.iter().flat_map(|r| [r[1], r[3]])).len(),
        rects.len(),
    )
}
pub fn crowded(values: &[f64]) -> usize {
    values
        .windows(2)
        .filter(|p| p[1] > p[0] && p[1] - p[0] < 0.1)
        .count()
}
pub fn intersect(a: &[(f64, f64)], b: &[(f64, f64)]) -> Vec<(f64, f64)> {
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
fn counts(bands: &[Band], refs: &[f64], axis: usize) -> HashMap<u64, usize> {
    let mut out = HashMap::new();
    for b in bands {
        if axis == 0 && !b.spans.is_empty() {
            for v in [b.a, b.b] {
                *out.entry(v.to_bits()).or_default() += 1;
            }
        }
        if axis == 1 {
            for &(l, h) in &b.spans {
                for v in [l, h] {
                    *out.entry(v.to_bits()).or_default() += 1;
                }
            }
        }
    }
    for &v in refs {
        *out.entry(v.to_bits()).or_default() += 1;
    }
    out
}
fn acceptable(
    bands: &[Band],
    chosen: &[Band],
    model: &Shape<'_>,
    original: &Shape<'_>,
    source: &Source<'_>,
    allowed: f64,
) -> Result<bool> {
    if score(&pack(bands)) >= score(&pack(chosen)) {
        return Ok(false);
    }
    if original.area()? - model.area()? > allowed + 1e-8
        || model.difference(original)?.area()? > 1e-8
    {
        return Ok(false);
    }
    Ok(source.topology(model)?["preserved"] == true)
}
struct Proposal {
    priority: (isize, isize, f64, usize),
    replacement: Vec<Band>,
}
pub fn coarsen(
    mut chosen: Vec<Band>,
    original: &Shape<'_>,
    source: &Source<'_>,
    allowed: f64,
    refs: &[Vec<f64>; 2],
) -> Result<Vec<Band>> {
    for _ in 0..12 {
        let mut bands: Vec<Band> = vec![];
        for b in chosen {
            if let Some(last) = bands.last_mut() {
                if last.spans == b.spans && last.b == b.a {
                    last.b = b.b;
                    continue;
                }
            }
            bands.push(b);
        }
        let model = original.engine.rectangles(&pack(&bands))?;
        let mut remaining = allowed - (original.area()? - model.area()?);
        let cx = counts(&bands, &refs[0], 0);
        let cy = counts(&bands, &refs[1], 1);
        let xs = unique(cx.keys().map(|&v| f64::from_bits(v)));
        let ys = unique(cy.keys().map(|&v| f64::from_bits(v)));
        let crowded_now = crowded(&xs) + crowded(&ys);
        let mut proposals = vec![];
        for (i, pair) in bands.windows(2).enumerate() {
            let (left, right) = (&pair[0], &pair[1]);
            if left.b != right.a || left.spans.is_empty() || right.spans.is_empty() {
                continue;
            }
            let spans = intersect(&left.spans, &right.spans);
            let mut alternatives = vec![];
            if left.spans.len() == right.spans.len() && spans.len() == left.spans.len() {
                alternatives.push(vec![Band {
                    a: left.a,
                    b: right.b,
                    spans,
                    depth: 0,
                    loss: 0.,
                }]);
            }
            let matches: Vec<_> = left
                .spans
                .iter()
                .enumerate()
                .flat_map(|(j, l)| {
                    right
                        .spans
                        .iter()
                        .enumerate()
                        .filter(move |(_, r)| l.1.min(r.1) > l.0.max(r.0) + 1e-8)
                        .map(move |(k, _)| (j, k))
                })
                .collect();
            for &(j, k) in &matches {
                if left.spans.len() == 1 && right.spans.len() == 1
                    || matches.iter().filter(|p| p.0 == j).count() != 1
                    || matches.iter().filter(|p| p.1 == k).count() != 1
                    || left.spans[j] == right.spans[k]
                {
                    continue;
                }
                let common = (
                    left.spans[j].0.max(right.spans[k].0),
                    left.spans[j].1.min(right.spans[k].1),
                );
                let (mut l, mut r) = (left.clone(), right.clone());
                l.spans[j] = common;
                r.spans[k] = common;
                alternatives.push(vec![l, r]);
            }
            for replacement in alternatives {
                let cost = (left.b - left.a) * left.spans.iter().map(|p| p.1 - p.0).sum::<f64>()
                    + (right.b - right.a) * right.spans.iter().map(|p| p.1 - p.0).sum::<f64>()
                    - replacement
                        .iter()
                        .map(|b| (b.b - b.a) * b.spans.iter().map(|p| p.1 - p.0).sum::<f64>())
                        .sum::<f64>();
                if cost > remaining + 1e-8 {
                    continue;
                }
                let old = counts(&pair.to_vec(), &[], 1);
                let new = counts(&replacement, &[], 1);
                let removed_y: HashSet<_> = old
                    .iter()
                    .filter(|(v, n)| {
                        cy.get(v).copied().unwrap_or(0) - **n + new.get(v).copied().unwrap_or(0)
                            == 0
                    })
                    .map(|(&v, _)| v)
                    .collect();
                let remove_x = replacement.len() == 1 && cx.get(&left.b.to_bits()) == Some(&2);
                let after = crowded(
                    &xs.iter()
                        .copied()
                        .filter(|v| !remove_x || *v != left.b)
                        .collect::<Vec<_>>(),
                ) + crowded(
                    &ys.iter()
                        .copied()
                        .filter(|v| !removed_y.contains(&v.to_bits()))
                        .collect::<Vec<_>>(),
                );
                proposals.push(Proposal {
                    priority: (
                        after as isize - crowded_now as isize,
                        -(removed_y.len() as isize) - isize::from(remove_x),
                        cost,
                        i,
                    ),
                    replacement,
                });
            }
        }
        proposals.sort_by(|a, b| {
            a.priority
                .0
                .cmp(&b.priority.0)
                .then(a.priority.1.cmp(&b.priority.1))
                .then(a.priority.2.total_cmp(&b.priority.2))
                .then(a.priority.3.cmp(&b.priority.3))
        });
        let mut selected = HashMap::new();
        let mut occupied = HashSet::new();
        for p in &proposals {
            let i = p.priority.3;
            if occupied.contains(&i)
                || occupied.contains(&(i + 1))
                || p.priority.2 > remaining + 1e-8
            {
                continue;
            }
            selected.insert(i, p.replacement.clone());
            occupied.insert(i);
            occupied.insert(i + 1);
            remaining -= p.priority.2;
        }
        if selected.is_empty() {
            return Ok(bands);
        }
        let candidate: Vec<_> = bands
            .iter()
            .enumerate()
            .filter(|(i, _)| *i == 0 || !selected.contains_key(&(i - 1)))
            .flat_map(|(i, b)| selected.get(&i).cloned().unwrap_or_else(|| vec![b.clone()]))
            .collect();
        let model = original.engine.rectangles(&pack(&candidate))?;
        if acceptable(&candidate, &bands, &model, original, source, allowed)? {
            chosen = candidate;
            continue;
        }
        chosen = bands;
        let mut chosen_model = original.engine.rectangles(&pack(&chosen))?;
        let mut accepted = 0;
        let mut indices: Vec<_> = selected.keys().copied().collect();
        indices.sort_unstable_by(|a, b| b.cmp(a));
        for i in indices {
            let old = original.engine.rectangles(&pack(&chosen[i..i + 2]))?;
            for p in proposals.iter().filter(|p| p.priority.3 == i) {
                let mut candidate = chosen[..i].to_vec();
                candidate.extend(p.replacement.clone());
                candidate.extend_from_slice(&chosen[i + 2..]);
                let patch = original.engine.rectangles(&pack(&p.replacement))?;
                let model = chosen_model.difference(&old.difference(&patch)?)?;
                if acceptable(&candidate, &chosen, &model, original, source, allowed)? {
                    chosen = candidate;
                    chosen_model = model;
                    accepted += 1;
                    break;
                }
            }
        }
        if accepted == 0 {
            break;
        }
    }
    Ok(chosen)
}
