# tools/ — 当前维护工具

从仓库根目录通过 `uv run --all-extras python tools/<脚本>` 运行。

| 脚本 | 用途 |
|---|---|
| `sync_out.py` | 按 `cases/` 同步预览、MaterialMap CSV、逐层掩膜 PNG、逐层 kx/ky/kz 热图与总索引；必须指定 `--px/--py`，可用 `--nx/--ny` 调整网格，`--force-equivalent` 只重算等效结果，`--force` 全部重建 |
| `census_cases.py` | 普查 ODB++ 样本的图元、极性和符号 |
| `fetch_spec.py` | 获取或校验未入库的官方格式规范 PDF |
| `extract_spec_text.py` | 按页提取规范文本，供格式语义核对 |

此前的 `review_*.py` 和 `benchmark_export.py` 属于一次性审阅/性能测量脚本，
记录保留在 `docs/reviews/`、`docs/fixes/`，持续回归由 `tests/` 承担，
因此不再保留这些证据脚本。`census_cases.py` 仍用于新样本的图元普查。
