# tools/ — 当前维护工具

从仓库根目录通过 `uv run --all-extras python tools/<脚本>` 运行。

| 脚本 | 用途 |
|---|---|
| `sync_out.py` | 按案例同步预览、MaterialMap JSON、运行诊断、掩膜、热图、region/网格诊断图与总索引；`--force-equivalent` 重算等效结果，`--force` 全部重建 |
| `audit_out.py` | 从最终 MaterialMap JSON 重建单元，独立核查域内包含、全局轴线、尺寸、完整体积、采样容量、Z 与物性范围；`--out` 选择案例输出根目录，`--output` 指定报告路径 |
| `benchmark_pipeline.py` | 独立进程测量 load / compose / export / equivalent / masks / materialmap / heatmaps 与峰值内存；`--repeat` 重复测量，`--profile` 定位热点；`--source-ref` 保存源码基线，`--compare` 对本次产物做 SHA-256 对照。基线须支持当前 JSON 与运行诊断接口 |
| `plot_grid.py` | 从正式 MaterialMap JSON 和 `diagnostics/run.json` 还原全局笛卡尔栅 PNG/SVG、局部放大及轴线统计；不重划几何 |
| `plot_regions.py` | 从中间 JSON 和实际运行诊断画 region PNG/SVG、原始轮廓、编号、尺寸表、面积与短边统计；默认输出到案例的 `regions/` |
| `refresh_viewer_outputs.py` | 仅刷新既有预览 HTML 的内联 CSS/JS，核对几何载荷不变；用于查看器样式和交互更新 |
| `census_cases.py` | 普查新 ODB++ 样本的图元、极性和符号 |
| `fetch_spec.py` | 获取或校验未入库的官方格式规范 PDF |
| `extract_spec_text.py` | 按页提取规范文本，供格式语义核对 |

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/audit_out.py
uv run --all-extras python tools/benchmark_pipeline.py --case minimal-test-odb --repeat 1 --output build/benchmark
```

阶段性的 `validate_architecture.py`、`validate_coarse_regions.py`、
`validate_drill_policy.py`、`validate_intermediate.py`、`validate_materialmap.py`、
`validate_performance_sampling.py`、`validate_viewer_performance.py` 已完成历史对照并退役。
其中持续需要的产物核查保留为 `audit_out.py`，通用基准保留为 `benchmark_pipeline.py`。
既有 `review_*.py` 和 `benchmark_export.py` 同样已退役；历史脚本可从相应 Git 提交读取，
结果与报告保留在 `docs/equivalence/`、`docs/reviews/` 和 `docs/fixes/`。

`build/` 用于可再生的日志、测试目录、基线源码副本和临时导出；验证完成后清理。
`cases/`、`external/`、官方规范、正式 `out/` 及 `build/git-backups/` 不作为临时缓存清理。
