# pcbkit / pcbeq

ODB++ v8.1 解析器与 PCB 物性等效器，分别安装、分别分发，只通过中间 JSON 协作。

| 软件 | 输入 | 输出 |
|---|---|---|
| `pcbkit` | ODB++ 目录、tar/zip；曲线与坐标精度 | `intermediate.json`、SVG、交互式 HTML |
| `pcbeq` | 中间 JSON；材料、光栅与网格参数 | `materialmap.json`、masks、heatmaps、regions、grids |

## 开发与运行

```powershell
uv sync --all-extras
uv run --all-extras pcbkit export cases/minimal-test-odb -o out/example/pcbkit --sagitta-mm 0.001 --digits 4
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --grid-level standard
```

显式光栅需同时提供 `--px` 和 `--py`，表示整板各方向像素数。专家可用 `--min-cell-mm`、`--max-cell-mm` 的一个值或 X/Y 两值替代网格档位。

默认材料缺省时明确警告并继续计算。逐层计算域、绝对 Z、厚度来源与可信状态由解析器交付；钻孔仅覆盖介质采样，采用实心铜模型。物性网格与 region 由物理几何确定，不受光栅分辨率影响。

## 协同测试管道

```powershell
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example --px 4096 --py 4096 --grid-level standard
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/audit_out.py
```

管道通过子进程调用两个 CLI，交接文件为 `out/<case>/pcbkit/intermediate.json`。等效产物位于 `pcbeq/`，运行状态与日志位于 `pipeline/`。可指定 `--pcbkit-exe`、`--pcbeq-exe`，调用不同环境的独立安装版本。

## 构建与测试

```powershell
uv build --package pcbkit --out-dir build/dist
uv build --package pcbeq --out-dir build/dist
uv run --all-extras python tools/verify_distributions.py
uv run --all-extras python tools/sync_contract.py --check
uv run --all-extras python -m pytest
```

两个 wheel/sdist 均自带模型和所需源码，不依赖另一个产品。权威 JSON 模型在 `contracts/intermediate.py`；修改后运行 `tools/sync_contract.py` 同步两侧与 schema，禁止独立手改副本。

## 文件架构

```text
contracts/             唯一中间 JSON 模型与生成 schema
packages/pcbkit/       解析器的 pyproject、README、src、tests
packages/pcbeq/        等效器的 pyproject、README、src、tests
tests/contracts/      两侧契约与语义校验
tests/integration/    真实 case、物理不变式与进程管道
tests/distribution/   安装依赖与导入边界
tools/                管道、批量同步、性能及分发验证工具
cases/                ODB++ 样本
docs/                 规范、调研、推导和验证记录
external/             只读参考项目
out/                  按 case 存放最终结果
```

`cases/` 整目录不纳入 Git，已有本地样本保留。使用真实案例的命令前需自行准备输入；缺少本地样本时，相关 pytest 测试跳过，`tests/fixtures/` 中的合成样本仍随源码提供。

完整边界、参数、输出发布和分发设计见 [独立 CLI 文档](docs/equivalence/10-independent-clis.md)。物理方法见 [等效器说明](docs/equivalence/01-material-equivalence.md)、[MaterialMap](docs/equivalence/09-region-materialmap.md)，模型字段见 [接口说明](docs/equivalence/07-intermediate-json.md)。维护命令见 [tools/README.md](tools/README.md)。旧 `odbpp` 命令和根 `src/odbpp` 已移除。
