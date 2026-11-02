# pcbkit-rust / pcbeq-rust

`dev/rust` 只维护本产品线，两产品通过中间 JSON 交接，分别构建和安装，不依赖另一条产品线的源码或分发包。
Rust 等效器完整处理 JSON → MaterialMap；包内 Python 只作启动和 MaterialMap 可视化。解析器自带 Python 输入和工程预览适配。

## 快速开始

```powershell
uv sync --extra test
uv run pcbkit-rust export cases/minimal-test-odb -o out/example/rust/pcbkit
uv run pcbeq-rust run out/example/rust/pcbkit/intermediate.json -o out/example/rust/pcbeq --grid-level standard
uv run pytest
cargo test --workspace
```

源码构建需要 Cargo、Rust 工具链与平台链接器。

## 项目结构

- `packages/pcbkit-rust/`、`packages/pcbeq-rust/`：Rust 核心与各自的 Python 适配/可视化；`crates/`：内部 Rust 几何库。
- `contracts/`：中间 JSON 契约；`tests/`：对应测试与手写夹具。
- `tools/`：维护工具；`docs/`：决策与领域资料。
- `cases/`、`out/`：本地案例与正式产物；`tmp/`：可再生任务文件。

## 文档索引

- [需求与验收](docs/requirements.md)；[协作规范](AGENTS.md)。
- 架构与接口：[独立产品与契约](docs/decisions/independent-products.md)、[Rust 数值核心](docs/decisions/rust-numerical-cores.md)、[输出与发布](docs/decisions/output-publication.md)、[中间 JSON](docs/interfaces/intermediate-json.md)、[MaterialMap](docs/interfaces/materialmap.md)。
- 解析：[解析语义](docs/decisions/pcbkit/parser-semantics.md)、[逐层域与叠层](docs/decisions/pcbkit/layer-domains-and-stackup.md)、[查看器交互](docs/decisions/pcbkit/viewer-interaction.md)。
- 等效：[内接 region](docs/decisions/pcbeq/inscribed-regions.md)、[region 局部网格](docs/decisions/pcbeq/region-local-cartesian-grid.md)、[材料采样](docs/decisions/pcbeq/material-sampling.md)、[材料模型](docs/decisions/pcbeq/material-model.md)、[pcbeq 可视化](docs/decisions/pcbeq/pcbeq-visualization.md)。
- 调研：[格式与单位](docs/research/format-and-units.md)、[几何与符号](docs/research/geometry-and-symbols.md)、[案例清单](docs/research/case-inventory.md)、[KiCad 案例](docs/research/kicad-test-cases.md)、[开源参考](docs/research/open-source-survey.md)、[Viewer 参考](docs/research/viewer-reference.md)。
- 参考：[官方规范与参考格式](docs/reference/format-sources.md)、[串并联热模型推导](docs/derivation/串并联PCB等效热模型推导.md)、[密度和热容推导](docs/derivation/密度和热容等效热模型推导.md)。
