# tools/ — 当前维护工具

从仓库根目录通过 `uv run --all-extras python tools/<脚本>` 运行。

| 脚本 | 用途 |
|---|---|
| `sync_out.py` | 按 `cases/` 同步预览、MaterialMap CSV、逐层掩膜 PNG、逐层 kx/ky/kz 热图与总索引；必须指定 `--px/--py`，`--min-cell-mm/--max-cell-mm` 指定 X/Y 单元边长约束，`--heatmap-grid` 画网格边界；`--force-equivalent` 只重算等效结果，`--force` 全部重建 |
| `plot_regions.py` | 从 preview-2 JSON 画真实 region 分区的 PNG/SVG、边缘放大图、面积/短边统计和逐矩形 CSV；默认输出到 case 的 `regions/`，需 tools extra 中的 Matplotlib |
| `census_cases.py` | 普查 ODB++ 样本的图元、极性和符号 |
| `fetch_spec.py` | 获取或校验未入库的官方格式规范 PDF |
| `extract_spec_text.py` | 按页提取规范文本，供格式语义核对 |

此前的 `review_*.py` 和 `benchmark_export.py` 属于一次性审阅/性能测量脚本，
记录保留在 `docs/reviews/`、`docs/fixes/`，持续回归由 `tests/` 承担，
因此不再保留这些证据脚本。`census_cases.py` 仍用于新样本的图元普查。
