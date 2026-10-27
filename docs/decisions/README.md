# 技术决策

每个主题描述当前设计、依据、适用范围与实现对照。功能和验收范围以 [需求](../../requirements.md) 为准。

| 决策 | 内容 |
| --- | --- |
| [独立产品与 JSON 契约](independent-products.md) | 两包边界、同源模型与进程交接 |
| [解析语义](parser-semantics.md) | 符号、扫掠、极性、量化及待真实样本复核的假设 |
| [逐层域与叠层](layer-domains-and-stackup.md) | profile 优先级、刚挠域、绝对 Z 和可信状态 |
| [内接 region](inscribed-regions.md) | 真实 Y 区间、双误差预算、局部细化与拓扑保护 |
| [全局笛卡尔栅](global-cartesian-grid.md) | 档位与专家尺寸、强制边界和传播统计 |
| [独立材料采样](material-sampling.md) | 扫描线、钻孔跨度、像素归属和容量检查 |
| [材料与物性模型](material-model.md) | 两相参数、默认来源、配置优先级与完整体积 |
| [查看器交互](viewer-interaction.md) | 图层、矩阵、钻孔箭头、坐标和性能边界 |
| [输出与发布](output-publication.md) | 产物布局、原子发布、返回码与复用 |

字段细节由 [接口文档](../interfaces/README.md) 维护，操作命令由 [开发说明](../development.md) 维护，公式由 [推导](../derivation/README.md) 维护。
