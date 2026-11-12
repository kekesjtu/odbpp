# pcbeq

独立 Rust CLI 消费 intermediate.json，完成校验、region 规划、geo 几何验收、局部网格、材料采样和等效，发布 materialmap.json 与数值诊断。计算程序无需 Python 或 GEOS。

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

`pcbeq run --help` 查看计算参数。标准/粗/细档位与专家尺寸互斥；默认标准档位。`--summary-json` 将机器可读摘要写到 stdout，告警和错误写到 stderr，成功返回 0，失败返回 2。材料文件和逐字段命令行覆盖由 Rust 统一解释。

## 可选 Python 管线与绘图

Python 包独立构建：`uv build --package pcbeq-rust`，不携带原生扩展或计算程序。计算程序放入 PATH，或通过环境变量和命令选项指定：

```powershell
$env:PCBEQ_EXE = (Resolve-Path target/release/pcbeq.exe).Path
uv run pcbeq-rust run intermediate.json -o result --grid-level standard
uv run pcbeq-rust run intermediate.json -o result --pcbeq-exe path/to/pcbeq.exe --max-cell-mm 2
uv run pcbeq-rust plot result/materialmap.json
```

`run` 将计算参数原样传递给独立进程，随后绘图；Python 不参与数值计算。`plot` 仅消费 MaterialMap，无需计算程序。图表使用 `--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 控制，也可设置热图尺寸和色域。

每个 region 独立采样；显式 `--px/--py` 使用整板参考采样相位。产物事务与绘图失败行为见[输出与发布](../../docs/decisions/output-publication.md)。
