# pcbkit / pcbeq

ODB++ v8.1 单板解析器 `pcbkit` 与 PCB 物性等效器 `pcbeq` 分别安装和分发，通过中间 JSON 交接。

## 快速开始

在项目根目录使用 uv：

```powershell
uv sync --all-extras
uv run --all-extras pcbkit export cases/minimal-test-odb -o out/example/pcbkit --sagitta-mm 0.001 --digits 4
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --grid-level standard
uv run --all-extras pytest
uv run --all-extras python tools/sync_contract.py --check
```

独立构建使用 `uv build --package pcbkit` 和 `uv build --package pcbeq`。命令参数可通过对应命令的 `--help` 查看，案例管道和维护工具见 [tools/README.md](tools/README.md)。

## 项目结构

- `packages/pcbkit/`：ODB++ 解析、正向实体和预览。
- `packages/pcbeq/`：内接 region、网格、材料等效及可视化。
- `contracts/`：共享中间 JSON 模型和 schema。
- `tests/`：跨包测试和夹具；`tools/`：长期维护工具。
- `cases/`：本地 PCB 案例；`out/`：案例产物。
- `docs/`：需求、决策、领域参考与调研；`tmp/`、`build/`：可再生工作文件与构建中间文件。

## 文档索引

- [需求与验收](docs/requirements.md)；[协作规范](AGENTS.md)。
- 架构与接口：[独立产品与契约](docs/decisions/independent-products.md)、[中间 JSON](docs/interfaces/intermediate-json.md)、[MaterialMap](docs/interfaces/materialmap.md)。
- 解析：[解析语义](docs/decisions/parser-semantics.md)、[逐层域与叠层](docs/decisions/layer-domains-and-stackup.md)、[查看器交互](docs/decisions/viewer-interaction.md)。
- 等效：[内接 region](docs/decisions/inscribed-regions.md)、[全局网格](docs/decisions/global-cartesian-grid.md)、[材料采样](docs/decisions/material-sampling.md)、[材料模型](docs/decisions/material-model.md)、[pcbeq 可视化](docs/decisions/pcbeq-visualization.md)、[输出与发布](docs/decisions/output-publication.md)。
- 调研：[格式与单位](docs/research/format-and-units.md)、[几何与符号](docs/research/geometry-and-symbols.md)、[案例清单](docs/research/case-inventory.md)、[KiCad 案例](docs/research/kicad-test-cases.md)、[开源参考](docs/research/open-source-survey.md)、[Viewer 参考](docs/research/viewer-reference.md)。
- 参考：[官方规范与参考格式](docs/reference/format-sources.md)、[串并联热模型推导](docs/derivation/串并联PCB等效热模型推导.md)、[密度和热容推导](docs/derivation/密度和热容等效热模型推导.md)。
