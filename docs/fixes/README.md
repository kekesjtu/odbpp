# docs/fixes/ — 修复与验证记录

这里记录已确认问题的根因、修改和验证结果，文件从 `01` 连续编号。
原始问题及复审证据见 [`../reviews/`](../reviews/README.md)，其编号独立。
旧修复记录中的 `tools/review_*.py`、`tools/benchmark_export.py` 是当时的
一次性审阅/性能测量命令，脚本已退役；
当前验证以 `tests/` 和 `tools/sync_out.py` 为准。

| 修复 | 对应审阅 | 内容 |
|---|---|---|
| [01 项目审阅修复](01-project-review-fixes.md) | [审阅 01](../reviews/01-project-review.md) | 七项问题的修正与验证 |
| [02 复审问题修复](02-followup-review-fixes.md) | [审阅 02](../reviews/02-followup-review.md) | 几何问题、绝对 Z 和英制铜厚 |
| [03 输出一致性修复](03-output-consistency-fixes.md) | [审阅 03](../reviews/03-output-consistency-review.md) | 厚度标记与导出一致性 |
| [04 导出正确性修复](04-export-correctness-fixes.md) | [审阅 05](../reviews/05-export-performance-review.md) | HTML 控件、零位精度和 profile 规整 |
| [05 物性等效修复](05-material-equivalence-fixes.md) | 本轮边界审阅 | 钻孔拓扑、域覆盖率、材料校验、矩形化致命、来源与公式 |
| [06 矩形化 slab 丢失修复](06-rectify-slab-loss.md) | 可视化对照复现 | 亚容差狭缝丢 slab、跨缺口合并、`rectify.area_warning`/`slab_ambiguous` |
| [07 图片长宽比修复](07-picture-aspect.md) | 全案例导出对照 | 掩膜被拉成正方形、热图被竖向拉伸、掩膜回调补上像素间距 |
| [08 可视化导出审阅修复](08-visualization-output-review.md) | 等效器可视化审阅 | 层名碰撞时保留全部掩膜与热图、默认材料警告在命令行显示 |

| [09 全局网格与内接架构](09-inscribed-architecture.md) | 新决策与全案例核查 | 竖条阶梯、全局笛卡尔栅、档位/专家模式、密集采样、完整体积及性能/收敛证据 |
