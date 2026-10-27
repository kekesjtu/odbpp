# tools — 当前维护工具

本目录只保留当前 CLI 开发、测试、结果维护和分发所需的工具。从仓库根目录运行：

```powershell
uv sync --all-extras
uv run --all-extras python tools/<脚本> --help
```

`cases/` 是被 Git 忽略的本地样本目录。运行真实 case 的管道、批量同步、分发验证前，需要自行准备相应 ODB++ 样本；合成测试样本仍保存在 `tests/fixtures/`。

| 工具 | 用途与主要参数 | 输入与输出 |
|---|---|---|
| `run_pipeline.py` | 单个 case 的协同测试；通过两个子进程串联 CLI；`--pcbkit-exe`、`--pcbeq-exe` 可指定独立环境入口 | ODB++ → `pcbkit/`、`pcbeq/` 产物及 `pipeline/` 日志、返回码、耗时 |
| `sync_out.py` | 批量维护 case 输出并生成总索引；默认复用有效结果，`--force` 全部重建，`--force-equivalent` 只重建等效阶段 | 本地 `cases/` → `out/<case>/` 和 `out/index.html` |
| `sync_contract.py` | 从唯一权威源同步两侧模型与 schema；`--check` 只检查，副本不一致时返回非零 | `contracts/intermediate.py` → 两个包内模型、`contracts/intermediate.schema` 和 `docs/intermediate.schema` |
| `verify_distributions.py` | 构建 wheel/sdist，从 sdist 重建；独立环境安装、移除 ODB++ 后消费 JSON、跨环境管道验证；依赖已缓存时可用 `--offline` | 两个包及本地最小样本 → 默认 `build/distribution-check/` 下的安装环境、产物和验证记录 |
| `benchmark_pipeline.py` | 测量 CLI 总墙钟时间、进程内峰值内存、region/网格数和数值产物哈希；`--repeat` 重复，`--compare` 对照既有报告 | 指定 `--case` → `--output` 目录中的产物及 `benchmark.json` |
| `audit_out.py` | 独立核查最终单元的层域、Z、内接性、网格尺寸、采样容量及物性范围；`--out` 指定产物根目录，`--output` 指定报告 | 已生成的中间 JSON、MaterialMap 和运行诊断 → 审计报告 |
| `validate_region_accuracy.py` | 核对真实板型的两项几何预算、连续轮廓与两种 Px/Py 下的坐标一致性；可指定 `--case`、两个精度参数、`--baseline` 和 `--output` | 已有中间 JSON → 几何误差、规模、耗时、Python 分配峰值与参考检查报告 |
| `refresh_viewer_outputs.py` | 仅刷新已生成预览 HTML 的内联 CSS/JS，检查其余几何载荷不变；几何或标记变化应使用完整同步 | `--out` 下的预览 HTML → 原位更新页面和 `--report` 指定的验证记录 |

```powershell
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example --px 4096 --py 4096 --grid-level standard
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/audit_out.py
uv run --all-extras python tools/benchmark_pipeline.py --case minimal-test-odb --repeat 2 --output build/benchmark
uv run --all-extras python tools/verify_distributions.py
uv run --all-extras python tools/sync_contract.py --check
uv run --all-extras python tools/refresh_viewer_outputs.py
```

规范下载、PDF 转文本及早期案例普查脚本已移除，历史实现可以从 Git 历史恢复，不再保留专用 PDF 处理依赖。官方规范来源和校验信息见 [docs/README.md](../docs/README.md)。region/grid 绘图由 pcbeq 自身负责。

`build/` 用于可再生的日志、独立安装验证与临时产物，`out/` 保留各 case 的最终结果。更多说明见 [独立 CLI 文档](../docs/equivalence/10-independent-clis.md)。
