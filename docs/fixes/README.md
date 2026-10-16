# docs/fixes/ — 修复与验证记录

这里记录已确认问题的根因、修改和验证结果，文件从 `01` 连续编号。
原始问题及复审证据见 [`../reviews/`](../reviews/README.md)，其编号独立。
旧修复记录中的 `tools/review_*.py`、`tools/validate_*.py`、`tools/benchmark_export.py` 是当时的
一次性审阅/性能测量命令，脚本已退役；
历史脚本可从对应 Git 提交读取，验证报告继续保留；
当前验证以 `tests/`、`tools/sync_out.py` 和 `tools/audit_out.py` 为准。

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
| [10 粗 region 与 Viewer 交互](10-coarse-regions-and-viewer.md) | 用户反馈及实机参考 | 5% 内接预算、Y 截面统一、受拓扑保护的合并、矩阵与图元查询 |
| [12 Heatmap 阅读布局](12-heatmap-layout.md) | 用户要求整体重写及逐层 PNG 核对 | Matplotlib 三方向单图、公共色条、毫米坐标及 81 层更新 |
| [13 中间 JSON 与 Pydantic](13-intermediate-json.md) | 用户确认的接口精简 | 唯一层目录与域引用、无版本协商、Pydantic IO 边界、真实案例输出与性能对照 |
| [14 介质 DRILL 处理后移](14-dielectric-drill-policy.md) | 用户明确更改材料规则 | JSON 保留未扣孔介质、仅绘图副本扣孔、铜层忽略 DRILL、真实案例与全量输出核查 |
| [15 移除 parse_info](15-remove-parse-info.md) | 用户明确精简接口 | 删除解析审计字段，保留内部统计，同步 schema 与全部案例输出 |
| [16 移除根 diagnostics](16-remove-root-diagnostics.md) | 用户明确精简接口 | 中间 JSON 不携带根诊断，保留内部诊断及逐层失败语义，同步注释代码与全部案例 |
| [17 Region MaterialMap JSON](17-region-materialmap.md) | 用户确认的输出格式精简 | 单个层级 JSON、未知 Z、行主序物性、严格读取与热图、全案例对照 |
| [18 DRILL 深度推导](18-drill-derived-z.md) | 用户要求删除冗余字段 | DRILL 不导出 Z，由 copper_span 端点铜层推导，铜层自身 Z 保留 |
| [19 项目清理](19-project-cleanup.md) | 用户要求清理过程文件、死代码与临时脚本 | 移除旧画布与闲置依赖、退役阶段脚本、保留独立输出审计并清除历史临时产物 |
