use clap::{Args, Parser, Subcommand};
use pcbeq::{
    options::{Mode, Options},
    pipeline,
};
use serde_json::{Value, json};
use std::{path::PathBuf, process::ExitCode};

#[derive(Parser)]
#[command(
    name = "pcbeq",
    version,
    about = "Compute equivalent PCB material properties from an intermediate board JSON.",
    long_about = "Compute equivalent PCB material properties from a board JSON exported by\n\
                  pcbkit-rust. This standalone CLI writes a MaterialMap and numerical diagnostics.",
    after_help = "Run 'pcbeq run --help' for calculation options, defaults, and examples."
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Compute a MaterialMap and write run diagnostics.
    #[command(
        long_about = "Compute cell-wise thermal conductivity (kx, ky, kz), density, and specific\n\
                      heat for the board's copper and dielectric layers. The run writes\n\
                      materialmap.json and diagnostics.json to the output directory. Unknown\n\
                      layer thickness or Z is recorded in diagnostics and the MaterialMap."
    )]
    Run(Run),
}

#[derive(Args)]
#[command(after_long_help = r#"Material configuration
  Use copper and base as the two phases. Their fields are k_w_mk,
  density_kg_m3, and specific_heat_j_kgk. Specify finite positive values.
  Omit a field or use null to fall through to the next level.
  Example file with a global base conductivity and a layer override
  {
    "global": {"base": {"k_w_mk": 0.35}},
    "layers": {"DIELECTRIC_1": {"base": {"k_w_mk": 0.40}}}
  }
  Each field is resolved from layers, layer_types, global, defaults, then the
  built-in values. The material flags set individual global fields and take
  precedence over the same global fields from inline JSON and the file.
  Built-in k, density, and specific heat are 385, 8960, 385 for copper and
  0.3, 1850, 1100 for the base. Their use is recorded as a warning.

Examples
  pcbeq run intermediate.json -o result
  pcbeq run intermediate.json -o result --grid-level fine --mode 2
  pcbeq run intermediate.json -o result --max-cell-mm 2 3 --materials materials.json --base-k 0.35

Exit status
  0 means the MaterialMap was written. A blocking failure returns 2.
  Warnings and errors are written to stderr."#)]
struct Run {
    /// Intermediate board JSON exported by pcbkit-rust, with geometry in millimetres.
    #[arg(value_name = "INTERMEDIATE_JSON")]
    path: PathBuf,
    /// Output directory for materialmap.json and diagnostics.json.
    ///
    /// Defaults to out/<input file stem> relative to the current directory.
    #[arg(short, long, value_name = "DIR", help_heading = "Output options")]
    out: Option<PathBuf>,
    /// Reference sampling pixels across the board bounding box in X.
    ///
    /// Supply both --px and --py as positive integers. With neither set, automatic
    /// sampling starts at 4096 pixels along the board's longer axis and can refine
    /// each region locally. Explicit sampling keeps the reference pixel spacing and
    /// centers and fails if cells are undersampled. Sampling leaves the grid unchanged.
    #[arg(
        verbatim_doc_comment,
        long,
        value_name = "PIXELS",
        requires = "py",
        help_heading = "Sampling options"
    )]
    px: Option<usize>,
    /// Reference sampling pixels across the board bounding box in Y. Requires --px.
    #[arg(
        long,
        value_name = "PIXELS",
        requires = "px",
        help_heading = "Sampling options"
    )]
    py: Option<usize>,
    /// Grid preset based on board size and copper features.
    ///
    /// Coarse uses larger cells and fine uses smaller cells. The default is standard
    /// when neither grid option is supplied. Mutually exclusive with --max-cell-mm.
    #[arg(verbatim_doc_comment, long, value_name = "LEVEL", value_parser = ["coarse", "standard", "fine"],
          conflicts_with = "max_cell_mm", help_heading = "Geometry and grid options")]
    grid_level: Option<String>,
    /// Maximum cell edge length in millimetres.
    ///
    /// Supply one finite positive size for both axes or two sizes in X, Y order.
    /// Each region is subdivided independently into cells within these limits.
    /// Mutually exclusive with --grid-level.
    #[arg(verbatim_doc_comment, long, value_name = "MM", num_args = 1..=2,
          help_heading = "Geometry and grid options")]
    max_cell_mm: Option<Vec<f64>>,
    /// Maximum relative area error for each effective layer profile.
    ///
    /// This is (lost area + added area) / original area. Use a ratio from 0
    /// inclusive to 1 exclusive, for example 0.01 for 1%. The approximation
    /// must also preserve islands, holes, and positive-width connections.
    #[arg(
        verbatim_doc_comment,
        long,
        value_name = "RATIO",
        default_value_t = 0.01,
        help_heading = "Geometry and grid options"
    )]
    max_area_error_ratio: f64,
    /// In-plane thermal conductivity model.
    #[arg(
        long,
        value_enum,
        default_value = "1",
        help_heading = "Material options"
    )]
    mode: Mode,
    /// Material configuration file containing a JSON object.
    #[arg(long, value_name = "FILE", help_heading = "Material options")]
    materials: Option<PathBuf>,
    /// Inline material configuration JSON object, merged over --materials.
    ///
    /// Replaces matching fields under global and defaults. Under layers and
    /// layer_types, each supplied phase replaces the entire matching phase object.
    #[arg(
        verbatim_doc_comment,
        long,
        value_name = "JSON",
        help_heading = "Material options"
    )]
    materials_json: Option<String>,
    /// Global copper thermal conductivity in W/(m K).
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    copper_k: Option<f64>,
    /// Global copper density in kg/m^3.
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    copper_density: Option<f64>,
    /// Global copper specific heat in J/(kg K).
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    copper_cp: Option<f64>,
    /// Global base thermal conductivity in W/(m K).
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    base_k: Option<f64>,
    /// Global base density in kg/m^3.
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    base_density: Option<f64>,
    /// Global base specific heat in J/(kg K).
    #[arg(long, value_name = "VALUE", help_heading = "Material options")]
    base_cp: Option<f64>,
    /// Print the run summary as JSON on stdout.
    ///
    /// The default is a text summary. Both formats still write the output files.
    #[arg(long, help_heading = "Output options")]
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
            mode: self.mode,
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
            for kind in ["warnings", "errors"] {
                for issue in summary["issues"][kind].as_array().into_iter().flatten() {
                    eprintln!(
                        "{}: {}: {} (layers: {})",
                        if kind == "warnings" {
                            "warning"
                        } else {
                            "error"
                        },
                        issue["code"].as_str().unwrap_or_default(),
                        issue["message"].as_str().unwrap_or_default(),
                        issue["layers"]
                    );
                }
            }
            if args.summary_json {
                println!("{summary}");
            } else {
                println!(
                    "wrote materialmap.json: {} cells, {} regions; compute {:.6} s",
                    summary["cells"],
                    summary["regions"],
                    summary["performance"]["total_s"].as_f64().unwrap()
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
