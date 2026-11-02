//! Shared physical-domain partition and constrained adjacent-interval merges.
use crate::{
    coarsen::{Rect, area, crowded, unique},
    geos::{Result, Shape},
    topology::Source,
};
use serde_json::{Value, json};
#[derive(Clone)]
pub struct Tagged {
    pub rect: Rect,
    pub owners: Vec<usize>,
}
pub fn exact_merge(mut tagged: Vec<Tagged>, physical: &[usize]) -> Vec<Tagged> {
    tagged.retain(|r| r.owners.iter().any(|i| physical.contains(i)));
    loop {
        let initial = tagged.len();
        for axis in 0..2 {
            let mut groups: Vec<Vec<Tagged>> = vec![];
            for r in tagged {
                let found = groups.iter().position(|g| {
                    g[0].owners == r.owners
                        && if axis == 0 {
                            g[0].rect[1] == r.rect[1] && g[0].rect[3] == r.rect[3]
                        } else {
                            g[0].rect[0] == r.rect[0] && g[0].rect[2] == r.rect[2]
                        }
                });
                if let Some(i) = found {
                    groups[i].push(r);
                } else {
                    groups.push(vec![r]);
                }
            }
            tagged = vec![];
            for mut group in groups {
                group.sort_by(|a, b| a.rect[axis].total_cmp(&b.rect[axis]));
                let mut current = group.remove(0);
                for next in group {
                    if current.rect[axis + 2] == next.rect[axis] {
                        current.rect[axis + 2] = next.rect[axis + 2];
                    } else {
                        tagged.push(current);
                        current = next;
                    }
                }
                tagged.push(current);
            }
        }
        if initial == tagged.len() {
            break;
        }
    }
    tagged.sort_by(|a, b| {
        a.rect
            .iter()
            .zip(b.rect)
            .map(|(a, b)| a.total_cmp(&b))
            .find(|c| !c.is_eq())
            .unwrap_or_else(|| a.owners.cmp(&b.owners))
    });
    tagged
}
fn score(rows: &[Tagged]) -> (usize, usize, usize) {
    let xs = unique(rows.iter().flat_map(|r| [r.rect[0], r.rect[2]]));
    let ys = unique(rows.iter().flat_map(|r| [r.rect[1], r.rect[3]]));
    (xs.len() + ys.len(), crowded(&ys), rows.len())
}
fn model<'a>(original: &Shape<'a>, rows: &[Tagged], domain: usize) -> Result<Shape<'a>> {
    original.engine.rectangles(
        &rows
            .iter()
            .filter(|r| r.owners.contains(&domain))
            .map(|r| r.rect)
            .collect::<Vec<_>>(),
    )
}
struct Proposal {
    key: (usize, f64, f64, usize),
    affected: Vec<usize>,
    replacements: Vec<Rect>,
    candidate: Vec<Tagged>,
}
pub fn merge<'a>(
    mut rows: Vec<Tagged>,
    originals: &[Shape<'a>],
    limit: f64,
    ratio: f64,
) -> Result<(Vec<Tagged>, Value)> {
    let sources = originals
        .iter()
        .map(|s| Source::new(s, limit))
        .collect::<Result<Vec<_>>>()?;
    let mut models = originals
        .iter()
        .enumerate()
        .map(|(i, s)| model(s, &rows, i))
        .collect::<Result<Vec<_>>>()?;
    let mut accepted = 0;
    let mut rejected = 0;
    for _ in 0..256 {
        let before = score(&rows);
        let mut columns: Vec<Vec<usize>> = vec![];
        for (i, r) in rows.iter().enumerate() {
            let column = columns
                .iter()
                .position(|g| rows[g[0]].rect[0] == r.rect[0] && rows[g[0]].rect[2] == r.rect[2]);
            if let Some(n) = column {
                columns[n].push(i);
            } else {
                columns.push(vec![i]);
            }
        }
        let mut blocks: Vec<(Rect, Vec<usize>)> = vec![];
        for mut column in columns {
            column.sort_by(|&a, &b| rows[a].rect[1].total_cmp(&rows[b].rect[1]));
            let mut current: Vec<usize> = vec![];
            let mut high = 0.;
            for i in column {
                if !current.is_empty() && rows[i].rect[1] != high {
                    let first = rows[current[0]].rect;
                    blocks.push((
                        [first[0], first[1], first[2], high],
                        std::mem::take(&mut current),
                    ));
                }
                current.push(i);
                high = rows[i].rect[3];
            }
            let first = rows[current[0]].rect;
            blocks.push(([first[0], first[1], first[2], high], current));
        }
        let mut proposals = vec![];
        for (left, li) in &blocks {
            for (right, ri) in &blocks {
                if left[2] != right[0] {
                    continue;
                }
                let (low, high) = (left[1].max(right[1]), left[3].min(right[3]));
                if high <= low || (left[1] == right[1] && left[3] == right[3]) {
                    continue;
                }
                let mut affected = li.clone();
                affected.extend(ri);
                affected.sort_unstable();
                affected.dedup();
                for (merge_low, merge_high) in [(true, true), (true, false), (false, true)] {
                    let mut replacements = rows.iter().map(|r| r.rect).collect::<Vec<_>>();
                    for &i in &affected {
                        if merge_low {
                            replacements[i][1] = replacements[i][1].max(low);
                        }
                        if merge_high {
                            replacements[i][3] = replacements[i][3].min(high);
                        }
                    }
                    let candidate = exact_merge(
                        rows.iter()
                            .enumerate()
                            .filter(|(i, _)| {
                                !affected.contains(i) || replacements[*i][3] > replacements[*i][1]
                            })
                            .map(|(i, r)| Tagged {
                                rect: replacements[i],
                                owners: r.owners.clone(),
                            })
                            .collect(),
                        &(0..originals.len()).collect::<Vec<_>>(),
                    );
                    let after = score(&candidate);
                    if after >= before {
                        continue;
                    }
                    let cost = affected
                        .iter()
                        .map(|&i| area(rows[i].rect) - area(replacements[i]))
                        .sum::<f64>();
                    let removed = before.1 as isize - after.1 as isize;
                    proposals.push(Proposal {
                        key: (
                            usize::from(removed <= 0),
                            cost / removed.max(1) as f64,
                            cost,
                            proposals.len(),
                        ),
                        affected: affected.clone(),
                        replacements,
                        candidate,
                    });
                }
            }
        }
        proposals.sort_by(|a, b| {
            a.key
                .0
                .cmp(&b.key.0)
                .then(a.key.1.total_cmp(&b.key.1))
                .then(a.key.2.total_cmp(&b.key.2))
                .then(a.key.3.cmp(&b.key.3))
        });
        let mut changed = false;
        for proposal in proposals {
            let mut losses = vec![];
            for &i in &proposal.affected {
                let old = originals[0].engine.rectangle(rows[i].rect)?;
                let r = proposal.replacements[i];
                losses.push((
                    i,
                    if r[3] > r[1] {
                        old.difference(&originals[0].engine.rectangle(r)?)?
                    } else {
                        old
                    },
                ));
            }
            let domains = proposal
                .affected
                .iter()
                .flat_map(|&i| rows[i].owners.iter().copied())
                .collect::<std::collections::BTreeSet<_>>();
            let mut updates = vec![];
            let mut good = true;
            for domain in domains {
                let patches = losses
                    .iter()
                    .filter(|(i, _)| rows[*i].owners.contains(&domain))
                    .map(|(_, s)| s.copy())
                    .collect::<Result<Vec<_>>>()?;
                let lost = originals[domain].engine.union(&patches)?;
                let model = models[domain].difference(&lost)?;
                let original = &originals[domain];
                if original.difference(&model)?.area()? > original.area()? * ratio + 1e-8
                    || model.difference(original)?.area()? > 1e-8
                    || !sources[domain].contour(&model, false)?.within
                    || sources[domain].topology(&model)?["preserved"] != true
                {
                    good = false;
                    break;
                }
                updates.push((domain, model));
            }
            if good {
                rows = proposal.candidate;
                for (i, model) in updates {
                    models[i] = model;
                }
                accepted += 1;
                changed = true;
                break;
            }
            rejected += 1;
        }
        if !changed {
            break;
        }
    }
    Ok((
        rows,
        json!({"accepted_adjacent_common_intervals":accepted,"constraint_rejections":rejected,
        "maximum_accepted_merges":256,"endpoint_source":"adjacent true interval intersection"}),
    ))
}
