# tools — 当前维护工具

从仓库根目录使用 `uv run --all-extras python tools/<脚本>`。

| 工具 | 用途 |
|---|---|
| `run_pipeline.py` | 实际调用 pcbkit、pcbeq 两个进程，保存交接文件、日志、返回码与耗时；可指定两个独立环境的 executable |
| `sync_out.py` | 按 case 复用同一管道，更新分目录产物和总索引；`--force` 全部重建，`--force-equivalent` 只重建等效阶段 |
| `sync_contract.py` | 从 contracts/intermediate.py 同步两个包内的模型及生成 schema；`--check` 只检查 |
| `verify_distributions.py` | 构建 wheel/sdist，从 sdist 重建；分别安装到干净环境，移除原始输入后验证 JSON 消费与跨环境管道 |
| `benchmark_pipeline.py` | 两个 CLI 的总墙钟时间、进程内峰值内存、region/网格数及数值产物哈希；支持 --repeat、--compare |
| `audit_out.py` | 对当前目录结构的最终 materialmap.json 独立核查层域、Z、内接、网格尺寸、采样容量及物性范围 |
| `refresh_viewer_outputs.py` | 仅刷新已生成 HTML 的内联样式和交互脚本 |
| `census_cases.py` | 普查 ODB++ 图元、极性与符号 |
| `fetch_spec.py` / `extract_spec_text.py` | 官方规范获取、校验与文本提取 |

```powershell
uv sync --all-extras
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example --px 4096 --py 4096 --grid-level standard
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/audit_out.py
uv run --all-extras python tools/benchmark_pipeline.py --case minimal-test-odb --repeat 2 --output build/benchmark
uv run --all-extras python tools/verify_distributions.py
```

region/grid 绘图已迁入 pcbeq，旧的 plot_regions.py、plot_grid.py 不再维护。旧 benchmark 的内核分段计时、源码快照及 profiler 参数已退役；当前脚本测量实际分发入口。

`build/` 用于可再生的日志、独立安装验证与临时产物，`out/` 保留各 case 的最终结果。更多说明见 [独立 CLI 文档](../docs/equivalence/10-independent-clis.md)。
