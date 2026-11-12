use clap::{Args, Parser, Subcommand};
use pcbeq::{options::Options, pipeline};
use serde_json::{Value, json};
use std::{path::PathBuf, process::ExitCode};

#[derive(Parser)]
#[command(
    name = "pcbeq",
    version,
    about = "Compute PCB material properties from intermediate JSON"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Publish MaterialMap and numerical diagnostics; no Python required.
    Run(Run),
}

#[derive(Args)]
struct Run {
    /// Intermediate board JSON in millimetres.
    path: PathBuf,
    #[arg(short, long)]
    out: Option<PathBuf>,
    #[arg(long, requires = "py")]
    px: Option<usize>,
    #[arg(long, requires = "px")]
    py: Option<usize>,
    #[arg(long, value_parser = ["coarse", "standard", "fine"], conflicts_with = "max_cell_mm")]
    grid_level: Option<String>,
    /// One common or two X/Y maximum cell sizes in mm.
    #[arg(long, num_args = 1..=2)]
    max_cell_mm: Option<Vec<f64>>,
    /// Maximum (lost + gained) area / original area, independently per profile.
    #[arg(long, default_value_t = 0.01)]
    max_area_error_ratio: f64,
    /// Material configuration JSON file.
    #[arg(long)]
    materials: Option<PathBuf>,
    /// Inline material configuration, merged over the file.
    #[arg(long, value_name = "JSON")]
    materials_json: Option<String>,
    #[arg(long)]
    copper_k: Option<f64>,
    #[arg(long)]
    copper_density: Option<f64>,
    #[arg(long)]
    copper_cp: Option<f64>,
    #[arg(long)]
    base_k: Option<f64>,
    #[arg(long)]
    base_density: Option<f64>,
    #[arg(long)]
    base_cp: Option<f64>,
    /// Write the machine-readable run summary to stdout.
    #[arg(long)]
    summary_json: bool,
}

impl Run {
    fn execute(&self) -> pcbeq::Result<Value> {
        let mut materials: Value = match &self.materials_json {
            Some(text) => {
                serde_json::from_str(text).map_err(|e| format!("invalid materials JSON: {e}"))?
            }
            None => json!({}),
        };
        if !materials.is_object() {
            return Err("materials must be a JSON object".into());
        }
        for (phase, fields) in [
            (
                "copper",
                [self.copper_k, self.copper_density, self.copper_cp],
            ),
            ("base", [self.base_k, self.base_density, self.base_cp]),
        ] {
            for (key, value) in ["k_w_mk", "density_kg_m3", "specific_heat_j_kgk"]
                .into_iter()
                .zip(fields)
            {
                if let Some(value) = value {
                    let global = materials
                        .as_object_mut()
                        .unwrap()
                        .entry("global")
                        .or_insert_with(|| json!({}));
                    let entry = global
                        .as_object_mut()
                        .ok_or("materials.global must be an object")?
                        .entry(phase)
                        .or_insert_with(|| json!({}));
                    entry
                        .as_object_mut()
                        .ok_or("material phase must be an object")?
                        .insert(key.into(), json!(value));
                }
            }
        }
        let output = self.out.clone().unwrap_or_else(|| {
            PathBuf::from("out").join(self.path.file_stem().unwrap_or_default())
        });
        let options = Options {
            px: self.px,
            py: self.py,
            grid_level: self
                .grid_level
                .clone()
                .or_else(|| self.max_cell_mm.is_none().then(|| "standard".into())),
            max_cell_mm: self.max_cell_mm.clone().unwrap_or_else(|| vec![5., 5.]),
            max_area_error_ratio: self.max_area_error_ratio,
            materials,
            materials_path: self
                .materials
                .as_ref()
                .map(|p| p.to_string_lossy().into_owned()),
        };
        pipeline::run(&self.path, &output, options)
    }
}

fn main() -> ExitCode {
    let Cli {
        command: Command::Run(args),
    } = Cli::parse();
    match args.execute() {
        Ok(summary) => {
            for warning in summary["warnings"].as_array().into_iter().flatten() {
                eprintln!("warning: {}", warning.as_str().unwrap_or_default());
            }
            if args.summary_json {
                println!("{summary}");
            } else {
                println!(
                    "wrote materialmap.json: {} cells, {} regions; compute {:.6} s",
                    summary["cells"],
                    summary["regions"],
                    summary["durations_s"]["total"].as_f64().unwrap()
                );
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
