//! Result-oriented diagnostics and disjoint wall-clock stage measurements.
use crate::{Result, document::Step, native_mesh::Mesh, planning::Geometry};
use serde_json::{Value, json};
use std::{fs, path::Path, time::Instant};

const STAGES: [&str; 9] = [
    "read_validate",
    "prepare",
    "profiles",
    "rectification",
    "mesh",
    "sampling",
    "equivalence",
    "write",
    "publication",
];

pub struct Diagnostics {
    pub value: Value,
    start: Instant,
    pub stage: &'static str,
}

impl Diagnostics {
    pub fn new() -> Self {
        let stages: serde_json::Map<String, Value> = STAGES
            .into_iter()
            .map(|name| (name.into(), Value::Null))
            .collect();
        Self {
            value: json!({
                "geometry":{"profile_count":null,"profiles":null},
                "issues":{"status":"running","warnings":[],"errors":[]},
                "accuracy":{"profiles":null},
                "performance":{"stages_s":stages,"total_s":null}
            }),
            start: Instant::now(),
            stage: "read_validate",
        }
    }

    pub fn measure<T>(
        &mut self,
        name: &'static str,
        work: impl FnOnce() -> Result<T>,
    ) -> Result<T> {
        self.stage = name;
        let start = Instant::now();
        let result = work();
        self.add_time(name, start.elapsed().as_secs_f64());
        result
    }

    pub fn add_time(&mut self, name: &'static str, seconds: f64) {
        let old = self.value["performance"]["stages_s"][name]
            .as_f64()
            .unwrap_or(0.);
        self.value["performance"]["stages_s"][name] = json!(old + seconds);
    }

    pub fn geometry(&mut self, step: &Step, geometry: &Geometry) {
        let mut profiles = serde_json::Map::new();
        let mut accuracy = serde_json::Map::new();
        for (id, audit) in geometry.report["profiles"].as_object().unwrap() {
            let layers: Vec<_> = step
                .layers
                .iter()
                .filter(|l| l.physical())
                .filter(|l| {
                    step.profile_for_layer(&l.name).is_some_and(|profile| {
                        geometry.report["profile_aliases"][&profile.id] == *id
                    })
                })
                .map(|l| l.name.clone())
                .collect();
            let ids = audit["regions"].as_array().unwrap();
            let sizes: Vec<_> = ids
                .iter()
                .map(|i| {
                    let r = geometry.rectangles[i.as_u64().unwrap() as usize];
                    [r[2] - r[0], r[3] - r[1]]
                })
                .collect();
            profiles.insert(
                id.clone(),
                json!({
                    "layer_count":layers.len(),"layers":layers,"region_count":ids.len(),
                    "region_size_mm":size_range(&sizes),"cell_count":null,"cell_size_mm":null
                }),
            );
            let a = audit["original_area_mm2"].as_f64().unwrap();
            let lost = audit["lost_area_mm2"].as_f64().unwrap();
            let gained = audit["gained_area_mm2"].as_f64().unwrap();
            let limit = geometry.report["max_area_error_ratio"].as_f64().unwrap();
            accuracy.insert(
                id.clone(),
                json!({
                    "area":{"original_mm2":a,"model_mm2":a-lost+gained,
                        "lost_mm2":lost,"gained_mm2":gained,"relative_error":(lost+gained)/a,
                        "allowed_relative_error":limit,"passed":lost+gained<=limit*a+1e-8},
                    "topology":audit["topology"]
                }),
            );
        }
        self.value["geometry"] = json!({"profile_count":profiles.len(),"profiles":profiles});
        self.value["accuracy"]["profiles"] = json!(accuracy);
    }

    pub fn mesh(&mut self, geometry: &Geometry, mesh: &Mesh) {
        for (id, audit) in geometry.report["profiles"].as_object().unwrap() {
            let mut sizes = vec![];
            let mut count = 0;
            for rid in audit["regions"].as_array().unwrap() {
                let rid = rid.as_u64().unwrap() as usize;
                let r = geometry.rectangles[rid];
                let (x, y) = &mesh.axes[rid];
                let nx = x.len() - 1;
                let ny = y.len() - 1;
                count += nx * ny;
                sizes.push([(r[2] - r[0]) / nx as f64, (r[3] - r[1]) / ny as f64]);
            }
            self.value["geometry"]["profiles"][id]["cell_count"] = json!(count);
            self.value["geometry"]["profiles"][id]["cell_size_mm"] = size_range(&sizes);
        }
    }

    pub fn finish(&mut self, error: Option<&str>) {
        if let Some(message) = error {
            self.value["issues"]["errors"]
                .as_array_mut()
                .unwrap()
                .push(json!({
                    "code":format!("{}.failed",self.stage),"stage":self.stage,
                    "message":message,"blocking":true
                }));
        }
        self.value["issues"]["status"] = json!(if error.is_some() {
            "failed"
        } else if !self.value["issues"]["errors"]
            .as_array()
            .unwrap()
            .is_empty()
        {
            "completed_with_errors"
        } else if !self.value["issues"]["warnings"]
            .as_array()
            .unwrap()
            .is_empty()
        {
            "completed_with_warnings"
        } else {
            "completed"
        });
        let total = self.start.elapsed().as_secs_f64();
        let measured: f64 = self.value["performance"]["stages_s"]
            .as_object()
            .unwrap()
            .iter()
            .filter(|(key, _)| key.as_str() != "other")
            .filter_map(|(_, value)| value.as_f64())
            .sum();
        self.value["performance"]["stages_s"]["other"] = json!((total - measured).max(0.));
        self.value["performance"]["total_s"] = json!(total);
    }

    pub fn write(&self, output: &Path) -> Result<()> {
        fs::create_dir_all(output).map_err(|e| e.to_string())?;
        let destination = output.join("diagnostics.json");
        if fs::symlink_metadata(&destination).is_ok_and(|m| m.is_symlink()) {
            return Err("diagnostics destination is a symlink".into());
        }
        let temporary = output.join(format!(".diagnostics-{}.tmp", std::process::id()));
        let mut created = false;
        let result = (|| {
            use std::io::Write;
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
                .map_err(|e| e.to_string())?;
            created = true;
            serde_json::to_writer_pretty(&mut file, &self.value).map_err(|e| e.to_string())?;
            file.write_all(b"\n").map_err(|e| e.to_string())?;
            drop(file);
            crate::native_output::replace(&temporary, &destination)
        })();
        if created && result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
}

fn size_range(sizes: &[[f64; 2]]) -> Value {
    let min: [f64; 2] =
        std::array::from_fn(|i| sizes.iter().map(|s| s[i]).reduce(f64::min).unwrap());
    let max: [f64; 2] =
        std::array::from_fn(|i| sizes.iter().map(|s| s[i]).reduce(f64::max).unwrap());
    json!({"min":{"x":min[0],"y":min[1]},"max":{"x":max[0],"y":max[1]}})
}
