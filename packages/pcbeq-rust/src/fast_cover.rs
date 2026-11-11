//! BMVC 2010 FAST, section 3.1: recursive search, edge growth, size gate.
use super::image::{Image, Integral, Window, area};
use crate::geos::Result;
use serde_json::{Value, json};

const MAX_RECTANGLES: usize = 10_000;
pub struct Cover {
    pub windows: Vec<Window>,
    pub audit: Value,
}

fn mark(mask: &mut [bool], width: usize, window: Window) {
    for y in window[1]..window[3] {
        mask[y * width + window[0]..y * width + window[2]].fill(true);
    }
}

pub fn cover(image: &Image, ratio: f64) -> Result<Cover> {
    let original = image.foreground.iter().filter(|&&p| p).count();
    if original == 0 {
        return Err("FAST input raster has no foreground pixels".into());
    }
    let size = image.width * image.height;
    let mut covered = vec![false; size];
    let mut disabled = vec![false; size];
    let mut threshold = size;
    let mut largest_rejected_seed = 0;
    let mut thresholds = vec![threshold];
    let (mut remaining, mut gained) = (original, 0usize);
    let (mut rejected, mut searches, mut moves) = (0, 0, 0);
    let mut windows = vec![];
    while (remaining + gained) as f64 > ratio * original as f64 {
        let integral = Integral::new(image, &covered, &disabled);
        let Some(seed) = integral.seed(image.width, image.height) else {
            if largest_rejected_seed == 0 {
                return Err(format!(
                    "FAST exhausted foreground: remaining={remaining}, gained={gained}, original={original}, budget={ratio}"
                ));
            }
            // Re-enable the temporarily rejected search regions and lower r to
            // the largest recursive seed found among those rejected candidates.
            threshold = largest_rejected_seed;
            thresholds.push(threshold);
            largest_rejected_seed = 0;
            disabled.fill(false);
            continue;
        };
        searches += 1;
        let (candidate, edge_moves) = integral.optimize(image.width, image.height, seed);
        moves += edge_moves;
        // On integer pixels the size threshold is inclusive; in particular a
        // one-pixel seed must remain selectable when r reaches one pixel.
        if area(candidate) < threshold {
            largest_rejected_seed = largest_rejected_seed.max(area(seed));
            mark(&mut disabled, image.width, candidate);
            rejected += 1;
            continue;
        }
        let foreground = integral.count(candidate);
        remaining -= foreground;
        gained += area(candidate) - foreground;
        mark(&mut covered, image.width, candidate);
        windows.push(candidate);
        if windows.len() > MAX_RECTANGLES {
            return Err("FAST exceeds 10000 rectangles".into());
        }
    }
    Ok(Cover {
        audit: json!({"tau":0.95,"pixels":[image.width,image.height],
        "original_foreground_pixels":original,"lost_pixels":remaining,"gained_pixels":gained,
        "relative_area_error":(remaining+gained)as f64/original as f64,
        "recursive_searches":searches,"edge_moves":moves,"rejected_candidates":rejected,
        "area_thresholds_pixels":thresholds,"rectangle_count":windows.len(),
        "seed":"equation (5) recursive maximum-benefit half-size windows",
        "benefit":"equation (4): sum(T-tau)","edge_search":"strictly improving single-pixel moves",
        "integer_halving":"floor, minimum one pixel","equal_score_order":"Y then X",
        "size_threshold":"inclusive on integer pixels","post_merge":false}),
        windows,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recursive_seed_uses_half_size_windows_instead_of_largest_foreground_rectangle() {
        let image = Image {
            width: 8,
            height: 8,
            foreground: vec![true; 64],
        };
        let integral = Integral::new(&image, &vec![false; 64], &vec![false; 64]);
        assert_eq!(integral.seed(8, 8), Some([0, 0, 4, 4]));
        assert_eq!(integral.optimize(8, 8, [0, 0, 4, 4]).0, [0, 0, 8, 8]);
    }

    #[test]
    fn size_gate_disables_and_restores_candidates_and_does_not_merge_them() {
        let image = Image {
            width: 8,
            height: 4,
            foreground: vec![
                true, true, true, true, true, true, true, true, true, true, true, true, true, true,
                true, true, true, true, true, true, false, false, false, false, true, true, true,
                true, false, false, false, false,
            ],
        };
        let result = cover(&image, 0.).unwrap();
        assert!(result.audit["rejected_candidates"].as_u64().unwrap() > 0);
        assert!(
            result.audit["area_thresholds_pixels"]
                .as_array()
                .unwrap()
                .len()
                > 1
        );
        let mut covered = vec![false; 32];
        for window in result.windows {
            for y in window[1]..window[3] {
                for x in window[0]..window[2] {
                    assert!(image.foreground[y * 8 + x]);
                    assert!(!covered[y * 8 + x]);
                    covered[y * 8 + x] = true;
                }
            }
        }
        assert_eq!(covered, image.foreground);
        assert_eq!(result.audit["post_merge"], false);
    }

    #[test]
    fn background_penalty_is_exact_and_stopping_error_is_symmetric_difference() {
        let mut foreground = vec![true; 25];
        foreground[0] = false;
        let image = Image {
            width: 5,
            height: 5,
            foreground,
        };
        let integral = Integral::new(&image, &vec![false; 25], &vec![false; 25]);
        assert_eq!(integral.benefit([0, 0, 5, 5]), 5);
        let result = cover(&image, 0.05).unwrap();
        assert!(result.audit["relative_area_error"].as_f64().unwrap() <= 0.05);
    }
}
