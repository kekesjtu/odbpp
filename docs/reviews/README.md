# docs/reviews/ — 审阅记录

这里按时间保留项目审阅、复审和性能测量的证据，文件从 `01` 连续编号。
对应修复见 [`../fixes/`](../fixes/README.md)，其编号独立。

旧报告中的 `tools/review_*.py`、`tools/validate_*.py` 命令记录当时的审阅和迁移过程。
这些一次性证据脚本已退役，报告与 JSON 结果继续保留；历史脚本可从对应 Git 提交读取。
当前几何、拓扑、Z 和输出精度由 `tests/` 持续验证，最终输出由 `tools/audit_out.py` 核查。
通用性能测量保留 `tools/benchmark_pipeline.py`，当前用法见 [工具目录](../../tools/README.md)。

| 审阅 | 内容 | 后续修复 |
|---|---|---|
| [01 项目审阅](01-project-review.md) | 四例完整导出与七项已确认问题 | [修复 01](../fixes/01-project-review-fixes.md) |
| [02 复审](02-followup-review.md) | 三项可复现问题 | [修复 02](../fixes/02-followup-review-fixes.md) |
| [03 输出一致性审阅](03-output-consistency-review.md) | 三项输出一致性和 Z 标记问题 | [修复 03](../fixes/03-output-consistency-fixes.md) |
| [04 修复复核](04-verification-review.md) | 独立复核第三轮修复 | — |
| [05 导出性能审阅](05-export-performance-review.md) | 性能瓶颈、兼容性和三项显示问题 | [修复 04](../fixes/04-export-correctness-fixes.md) |
| [06 全流程性能优化](06-pipeline-performance.md) | 解析、几何索引、采样和图像生成优化；四个真实样本的阶段耗时、内存和 192 个产物对照 | — |
| [07 HTML 交互性能优化](07-viewer-performance.md) | 按帧更新、坐标转换与矩阵布局优化；159 个浏览器交互检查点和 18 个页面非代码内容对照 | — |
