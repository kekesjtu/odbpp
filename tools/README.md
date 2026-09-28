# tools/ — 当前维护工具

从仓库根目录通过 `uv run --all-extras python tools/<脚本>` 运行。

| 脚本 | 用途 |
|---|---|
| `sync_out.py` | 按 `cases/` 生成/核验 `out/` 的每份输入预览与索引；KiCad 原生、裁剪分组；`--prune` 清理失配产物，`--force` 重建 |
| `census_cases.py` | 普查 ODB++ 样本的图元、极性和符号 |
| `fetch_spec.py` | 获取或校验未入库的官方格式规范 PDF |
| `extract_spec_text.py` | 按页提取规范文本，供格式语义核对 |
| `benchmark_export.py` | 按需测量完整导出耗时、文件摘要与回归差异 |

此前的三份 `review_*.py` 仅用于 2026-09-21 的一次性审阅。
审阅和修复过程仍保留在 `docs/reviews/`、`docs/fixes/`，持续回归由
`tests/` 承担，因此这些证据脚本已退役。
