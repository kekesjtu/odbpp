# docs/reviews/ — 审阅记录

这里按时间保留项目审阅、复审和性能测量的证据，文件从 `01` 连续编号。
对应修复见 [`../fixes/`](../fixes/README.md)，其编号独立。

旧报告中的 `tools/review_*.py` 命令记录当时的审阅过程。三份一次性
证据脚本已在清理中退役，相关几何、拓扑、Z 和输出精度回归由
`tests/` 持续验证；这些历史命令不再作为当前复现步骤。

| 审阅 | 内容 | 后续修复 |
|---|---|---|
| [01 项目审阅](01-project-review.md) | 四例完整导出与七项已确认问题 | [修复 01](../fixes/01-project-review-fixes.md) |
| [02 复审](02-followup-review.md) | 三项可复现问题 | [修复 02](../fixes/02-followup-review-fixes.md) |
| [03 输出一致性审阅](03-output-consistency-review.md) | 三项输出一致性和 Z 标记问题 | [修复 03](../fixes/03-output-consistency-fixes.md) |
| [04 修复复核](04-verification-review.md) | 独立复核第三轮修复 | — |
| [05 导出性能审阅](05-export-performance-review.md) | 性能瓶颈、兼容性和三项显示问题 | [修复 04](../fixes/04-export-correctness-fixes.md) |
