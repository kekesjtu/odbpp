# pcbeq

独立 Rust CLI 消费 intermediate.json，完成校验、region 规划、geo 几何验收、局部网格、材料采样和等效，发布 materialmap.json 与唯一的 diagnostics.json，不输出产物清单。计算程序无需 Python 或 GEOS。

## 构建与计算

开发构建：

```powershell
cargo build -p pcbeq-rust --release
target/release/pcbeq.exe run intermediate.json -o result --grid-level standard
target/release/pcbeq.exe run intermediate.json -o result --max-cell-mm 2 3 --max-area-error-ratio 0.01 --materials materials.json
```

Windows x64 单文件发布，在当前构建会话中静态链接 C 运行库：

```powershell
$env:RUSTFLAGS = "-C target-feature=+crt-static"
cargo build -p pcbeq-rust --release --target x86_64-pc-windows-msvc
```

交付 `target/x86_64-pc-windows-msvc/release/pcbeq.exe`；用户无需安装 Rust、Python 或 VC 运行库。构建环境需要 Rust 工具链及平台链接器。

`--grid-level` 按整板轮廓包围盒尺寸选择全板统一的目标边长，默认 `standard`。各 region 按该上限独立等分。需要指定毫米尺寸时，使用 `--max-cell-mm` 给出一个共用上限或 X/Y 两个上限，与档位互斥。公式见[几何规划](../../docs/decisions/pcbeq/geometry-planning.md#局部均匀网格)。

`pcbeq run --help` 查看计算参数。`--summary-json` 将机器可读摘要写到 stdout，告警和错误写到 stderr。计算完成返回 0，Z 不可信作为非阻断错误记录且正常生成 MaterialMap；阻断失败返回 2。诊断结构与统计口径见[运行诊断](../../docs/interfaces/pcbeq-diagnostics.md)。材料文件和逐字段命令行覆盖由 Rust 统一解释。

## 可选 Python 管线与绘图

原生 CLI 和 Python 管线均使用 `--mode 1`（默认）选择 2DVTN，使用 `--mode 2` 选择 SPTRA。两种模式的面内热导计算见[材料模型](../../docs/decisions/pcbeq/material-model.md)。

Python 包独立构建：`uv build --package pcbeq-rust`，不携带原生扩展或计算程序。计算程序放入 PATH，或通过环境变量和命令选项指定：

```powershell
$env:PCBEQ_EXE = (Resolve-Path target/release/pcbeq.exe).Path
uv run pcbeq-rust run intermediate.json -o result --grid-level standard
uv run pcbeq-rust run intermediate.json -o result --pcbeq-exe path/to/pcbeq.exe --max-cell-mm 2
uv run pcbeq-rust plot result/materialmap.json
```

`run` 将计算参数原样传递给独立进程，随后绘图；Python 不参与数值计算。`plot` 仅消费 MaterialMap，无需计算程序。图表使用 `--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 控制，也可设置热图尺寸和色域。

`--pixels-per-cell-axis N` 指定每个物性单元沿 X、Y 各采多少个像素，N 为正整数，默认 9。例如设为 16 时，每格固定采 16×16 个像素。产物事务与绘图失败行为见[输出与发布](../../docs/decisions/output-publication.md)。
