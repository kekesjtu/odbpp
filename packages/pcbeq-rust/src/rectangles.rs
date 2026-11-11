pub type Rect = [f64; 4];

pub fn area(r: Rect) -> f64 {
    (r[2] - r[0]).max(0.) * (r[3] - r[1]).max(0.)
}

pub fn unique(values: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut values: Vec<_> = values.collect();
    values.sort_by(f64::total_cmp);
    values.dedup();
    values
}

/// Remove full shared edges without changing the modeled geometry.
pub fn merge(mut rectangles: Vec<Rect>) -> Vec<Rect> {
    loop {
        let before = rectangles.len();
        for axis in 0..2 {
            let other = 1 - axis;
            rectangles.sort_by(|a, b| {
                a[other]
                    .total_cmp(&b[other])
                    .then(a[other + 2].total_cmp(&b[other + 2]))
                    .then(a[axis].total_cmp(&b[axis]))
            });
            let mut merged: Vec<Rect> = Vec::new();
            for rectangle in rectangles {
                if let Some(last) = merged.last_mut()
                    && last[other] == rectangle[other]
                    && last[other + 2] == rectangle[other + 2]
                    && last[axis + 2] == rectangle[axis]
                {
                    last[axis + 2] = rectangle[axis + 2];
                } else {
                    merged.push(rectangle);
                }
            }
            rectangles = merged;
        }
        if before == rectangles.len() {
            break;
        }
    }
    rectangles.sort_by(|a, b| {
        a.iter()
            .zip(b)
            .map(|(a, b)| a.total_cmp(b))
            .find(|ordering| !ordering.is_eq())
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rectangles
}
