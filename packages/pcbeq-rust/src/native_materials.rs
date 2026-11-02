use crate::geos::Result;
use serde_json::{Value, json};
const FIELDS: [&str; 3] = ["k_w_mk", "density_kg_m3", "specific_heat_j_kgk"];
const SOURCES: [&str; 2] = [
    "pure annealed copper at ~25 C; CRC Handbook of Chemistry and Physics",
    "typical cured FR-4 datasheet values, e.g. Isola 370HR",
];
pub fn defaults() -> Value {
    json!({
    "copper":{"k_w_mk":385.,"density_kg_m3":8960.,"specific_heat_j_kgk":385.,"source":SOURCES[0]},
    "base":{"k_w_mk":0.3,"density_kg_m3":1850.,"specific_heat_j_kgk":1100.,"source":SOURCES[1]}})
}
pub struct Materials {
    config: Value,
    pub warnings: Vec<String>,
    pub by_layer: serde_json::Map<String, Value>,
}
impl Materials {
    pub fn new(config: Value) -> Result<Self> {
        if !config.is_object() {
            return Err("materials must be a JSON object".into());
        }
        for section in ["defaults", "global"] {
            if let Some(value) = config.get(section) {
                validate_pairs(value, section)?;
            }
        }
        for section in ["layer_types", "layers"] {
            if let Some(value) = config.get(section) {
                let entries = value
                    .as_object()
                    .ok_or(format!("{section} must be an object"))?;
                for (key, entry) in entries {
                    validate_pairs(entry, &format!("{section}.{key}"))?;
                }
            }
        }
        Ok(Self {
            config,
            warnings: vec![],
            by_layer: serde_json::Map::new(),
        })
    }
    pub fn resolve(&mut self, name: &str, kind: &str) -> Result<[f64; 6]> {
        let mut values = [[385., 8960., 385.], [0.3, 1850., 1100.]];
        let mut provenance = serde_json::Map::new();
        for (index, phase) in ["copper", "base"].iter().enumerate() {
            let mut fields = serde_json::Map::new();
            let mut missing = vec![];
            for (field_index, field) in FIELDS.iter().enumerate() {
                let mut value = values[index][field_index];
                let mut origin = "project_default";
                for (key, entry) in [
                    ("user_default", &self.config["defaults"][phase]),
                    ("user_global", &self.config["global"][phase]),
                    ("user_layer_type", &self.config["layer_types"][kind][phase]),
                    ("layer_override", &self.config["layers"][name][phase]),
                ] {
                    if let Some(v) = entry[field].as_f64() {
                        value = v;
                        origin = key;
                    }
                }
                if !value.is_finite() || value <= 0. {
                    return Err(format!(
                        "layer {name:?}: {phase}.{field} must be finite and positive"
                    ));
                }
                values[index][field_index] = value;
                if origin == "project_default" {
                    missing.push(*field);
                }
                fields.insert(
                    field.to_string(),
                    json!({"value":value,"origin":origin,
                    "source":if origin=="project_default"{SOURCES[index]}else{origin}}),
                );
            }
            if !missing.is_empty() {
                let message = format!(
                    "materials.default_used: {phase} uses project defaults for {}",
                    missing.join(", ")
                );
                if !self.warnings.contains(&message) {
                    self.warnings.push(message);
                }
            }
            provenance.insert(
                phase.to_string(),
                json!({"k_w_mk":values[index][0],"density_kg_m3":values[index][1],
                "specific_heat_j_kgk":values[index][2],"fields":fields}),
            );
        }
        self.by_layer.insert(
            name.to_string(),
            json!({"phase1":"copper","phase2":"base","provenance":provenance}),
        );
        Ok([
            values[0][0],
            values[1][0],
            values[0][1],
            values[1][1],
            values[0][2],
            values[1][2],
        ])
    }
}
fn validate_pairs(value: &Value, path: &str) -> Result<()> {
    let pairs = value
        .as_object()
        .ok_or(format!("{path} must be an object"))?;
    for (phase, entry) in pairs {
        if !matches!(phase.as_str(), "copper" | "base") {
            return Err(format!("{path}: unknown material phase {phase:?}"));
        }
        let fields = entry
            .as_object()
            .ok_or(format!("{path}.{phase} must be an object"))?;
        for (key, value) in fields {
            if !FIELDS.contains(&key.as_str()) {
                return Err(format!("{path}.{phase}: unknown material field {key:?}"));
            }
            if !value.is_null() && value.as_f64().is_none_or(|v| !v.is_finite() || v <= 0.) {
                return Err(format!("{path}.{phase}.{key} must be finite and positive"));
            }
        }
    }
    Ok(())
}
