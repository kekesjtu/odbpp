# 维护工具

从仓库根目录使用项目 uv 环境：

```powershell
uv sync --all-extras
uv run --all-extras python tools/<脚本> --help
```

| 工具 | 用途 | 输入与输出 |
| --- | --- | --- |
| `run_pipeline.py` | 通过两个子进程串联 CLI，可指定独立环境入口 | ODB++ → pcbkit / pcbeq 结果及 pipeline 日志与耗时 |
| `sync_out.py` | 批量维护案例与总索引，按配置和指纹复用，支持 `--force`、`--force-equivalent` | 本地 cases → out |
| `sync_contract.py` | 从模型权威源同步包内模型与 schema，`--check` 检查一致性 | contracts → 两产品模型、contracts 与 docs/interfaces 下的 schema |
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

默认预热一次；导入、预热和基准报告不计时。每轮重新读取 JSON，计时包含几何规划、材料采样、物性等效和文件关闭/重命名，不要求物理磁盘落盘。Python 使用生产流式写入器；Rust 直接调用完整原生文件接口，不经过 Python 输入校验、几何规划或 MaterialMap 写入。报告记录每轮数据、最小/中位/最大耗时、CPU 时间、吞吐量、实际光栅及三维 region/单元数；Rust 同时记录几何、网格、采样、写入和 GEOS 运算耗时，诊断报告序列化不计入 MaterialMap 时间。等效失败时返回非零。默认网格与光栅策略和 `pcbeq run` 相同，可用 `--px/--py`、`--grid-level` 或 `--max-cell-mm` 控制负载。

正式案例按 `out/<case>/python/` 与 `out/<case>/rust/` 分别存放两种版本，各自包含 `pcbkit/`、`pcbeq/` 与 `pipeline/`。`sync_out.py` 维护 Python 产物，总索引保留两种版本；Rust 管道通过 `run_pipeline.py --pcbkit-exe pcbkit-rust --pcbeq-exe pcbeq-rust -o out/<case>/rust` 选择两个独立入口及输出位置。
几何、标记或元数据改变时使用完整案例同步；CSS/JS 专项更新可用预览刷新工具。

运行、测试和构建入口见 [项目说明](../README.md)，规范来源见 [参考资料](../docs/reference/format-sources.md)。
