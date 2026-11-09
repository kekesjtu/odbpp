# 维护工具

从仓库根目录使用项目 uv 环境。仅使用 Python 版时可省略 `--all-extras`。

```powershell
uv sync --all-extras
uv run --all-extras python tools/<脚本> --help
```

| 工具 | 用途 | 输入与输出 |
| --- | --- | --- |
| `run_pipeline.py` | 通过两个子进程串联 CLI，可指定独立环境入口 | ODB++ → pcbkit / pcbeq 结果及 pipeline 日志与耗时 |
| `sync_out.py` | 批量维护案例与总索引，`--backend python/rust` 选择独立产品线，按配置和代码指纹复用 | 本地 cases → out |
| `sync_contract.py` | 从模型权威源同步包内 Pydantic 模型与 schema，`--check` 检查一致性 | contracts → 随包 Python 模型、contracts 与 docs/interfaces 下的 schema |
| `verify_distributions.py` | 构建 wheel / sdist、独立安装、跨环境交接；`--backend rust` 验证 Rust 版，缓存可用时支持 `--offline` | 两产品与本地样本 → tmp/distribution-check |
| `benchmark_pcbeq.py` | 仅测中间 JSON 读取/校验至 MaterialMap 写入关闭，支持预热、重复和 Python/Rust 后端 | 中间 JSON → `--output` 下 materialmap.json 与 benchmark.json，不生成图片 |
| `audit_out.py` | 独立核查完整单元的域、Z、尺寸、几何预算、容量及物性 | out → 默认 tmp/validation/reports/output-validation.json |
| `validate_region_accuracy.py` | 检查面积预算、内接、拓扑与几何坐标独立性，可比较材料采样 | 中间 JSON → 默认 tmp/validation/reports/region-area-validation.json |
| `refresh_viewer_outputs.py` | 仅替换预览内联 CSS/JS 并检查其余载荷一致性 | 预览 HTML → 原位更新及默认 tmp/validation/reports/viewer-output-validation.json |

`cases/` 是本地真实输入目录。合成夹具位于 `tests/fixtures/`，测试和基准过程输出位于 `tmp/`，正式结果位于 `out/`。

```powershell
uv run python tools/benchmark_pcbeq.py out/minimal-test-odb/python/pcbkit/intermediate.json --output tmp/2026-11-02-pcbeq-benchmark/python --repeat 3
uv run --extra rust python tools/benchmark_pcbeq.py out/minimal-test-odb/python/pcbkit/intermediate.json --output tmp/2026-11-02-pcbeq-benchmark/rust --backend rust --repeat 3
```

默认预热一次。每轮从 JSON 读取和校验计时，包含几何规划、采样、等效和 MaterialMap 写入关闭及发布操作。导入、预热、诊断报告序列化、图片和物理磁盘落盘不计入 MaterialMap 耗时。报告包含各轮耗时、CPU 时间、吞吐量、实际光栅和模型规模。可用 `--px/--py`、`--grid-level` 或 `--max-cell-mm` 控制负载。

`sync_out.py` 用 `--backend python` 或 `--backend rust` 维护对应产物。运行单个 Rust 案例时可为 `run_pipeline.py` 指定 `--pcbkit-exe pcbkit-rust --pcbeq-exe pcbeq-rust` 和输出目录。目录约定见[输出与发布](../docs/decisions/output-publication.md)。几何、标记或元数据改变时使用完整案例同步，CSS/JS 专项更新可用预览刷新工具。

运行、测试和构建入口见 [项目说明](../README.md)，规范来源见 [参考资料](../docs/reference/format-sources.md)。
