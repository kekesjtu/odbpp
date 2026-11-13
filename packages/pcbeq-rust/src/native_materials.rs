use crate::Result;
use serde_json::{Value, json};
const FIELDS: [&str; 3] = ["k_w_mk", "density_kg_m3", "specific_heat_j_kgk"];
pub struct Materials {
    config: Value,
    pub warnings: Vec<Value>,
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
        })
    }
    pub fn resolve(&mut self, name: &str, kind: &str) -> Result<[f64; 6]> {
        let mut values = [[385., 8960., 385.], [0.3, 1850., 1100.]];
        for (index, phase) in ["copper", "base"].iter().enumerate() {
            let mut defaults = serde_json::Map::new();
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
                    defaults.insert(field.to_string(), json!(value));
                }
            }
            if !defaults.is_empty() {
                let defaults = json!(defaults);
                if let Some(warning) = self
                    .warnings
                    .iter_mut()
                    .find(|w| w["phase"] == *phase && w["default_values"] == defaults)
                {
                    warning["layers"].as_array_mut().unwrap().push(json!(name));
                } else {
                    self.warnings.push(json!({"code":"materials.default_used",
                        "message":format!("{phase} uses built-in material defaults"),
                        "layers":[name],"phase":phase,"default_values":defaults}));
                }
            }
        }
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
