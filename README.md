# pcbkit / pcbeq

ODB++ v8.1 单板解析器 `pcbkit` 与 PCB 物性等效器 `pcbeq` 分别安装和分发，通过中间 JSON 交接。
`pcbkit-rust` 与 `pcbeq-rust` 提供独立安装的 Rust 数值核心版本。各包的职责、语言边界与数据流见[项目架构](docs/architecture.md)。

## 快速开始

在项目根目录使用 uv。以下示例需要本地 `cases/minimal-test-odb`，样本来源见[案例清单](docs/research/case-inventory.md)。`cases/` 不随仓库检出，也可将输入换成自己的 ODB++ v8.1 单板目录或压缩包。

```powershell
uv sync --extra test
uv run pcbkit export cases/minimal-test-odb -o out/example/python/pcbkit --sagitta-mm 0.001 --digits 4
uv run pcbeq run out/example/python/pcbkit/intermediate.json -o out/example/python/pcbeq --grid-level standard
uv run --extra test pytest
uv run python tools/sync_contract.py --check
```

从源码安装 Rust 版本需要 Rust 工具链与平台链接器。常用数值参数与 Python 版一致。

```powershell
uv sync --all-extras
uv run --extra rust pcbkit-rust export cases/minimal-test-odb -o out/example/rust/pcbkit
uv run --extra rust pcbeq-rust run out/example/rust/pcbkit/intermediate.json -o out/example/rust/pcbeq --grid-level standard
uv run --all-extras pytest tests/rust
cargo test --workspace
```

独立构建使用 `uv build --package <包名>`，包名为 `pcbkit`、`pcbeq`、`pcbkit-rust` 或 `pcbeq-rust`。命令参数可通过对应命令的 `--help` 查看，案例管道和维护工具见 [tools/README.md](tools/README.md)。

## 项目结构

- `packages/pcbkit/`：ODB++ 解析、正向实体和预览。
- `packages/pcbeq/`：内接 region、网格、材料等效及可视化。
- `packages/pcbkit-rust/`、`packages/pcbeq-rust/`：两个 Rust 扩展及独立命令；`crates/`：内部共享 Rust 几何。
- `contracts/`：共享中间 JSON 模型和 schema。
- `tests/`：跨包测试和夹具；`tools/`：长期维护工具。
- `cases/`：本地 PCB 案例；`out/`：案例产物。
- `docs/`：需求、决策、领域参考与调研；`tmp/`、`build/`：可再生工作文件与构建中间文件。

## 文档索引

- [需求与验收](docs/requirements.md)；[协作规范](AGENTS.md)。
- 架构与接口：[项目架构](docs/architecture.md)、[输出与发布](docs/decisions/output-publication.md)、[中间 JSON](docs/interfaces/intermediate-json.md)、[MaterialMap](docs/interfaces/materialmap.md)。
- 解析：[解析语义](docs/decisions/pcbkit/parser-semantics.md)、[逐层轮廓与叠层](docs/decisions/pcbkit/layer-domains-and-stackup.md)、[查看器交互](docs/decisions/pcbkit/viewer-interaction.md)。
- 等效：[内接 region 与局部网格](docs/decisions/pcbeq/geometry-planning.md)、[材料采样](docs/decisions/pcbeq/material-sampling.md)、[材料模型](docs/decisions/pcbeq/material-model.md)、[pcbeq 可视化](docs/decisions/pcbeq/pcbeq-visualization.md)。
- 调研：[格式与单位](docs/research/format-and-units.md)、[几何与符号](docs/research/geometry-and-symbols.md)、[案例清单](docs/research/case-inventory.md)、[KiCad 案例](docs/research/kicad-test-cases.md)、[开源参考](docs/research/open-source-survey.md)、[Viewer 参考](docs/research/viewer-reference.md)。
- 参考：[官方规范与参考格式](docs/reference/format-sources.md)、[串并联热模型推导](docs/derivation/串并联PCB等效热模型推导.md)、[密度和热容推导](docs/derivation/密度和热容等效热模型推导.md)。
