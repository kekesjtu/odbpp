# pcbeq-rust 工程图表

图表从已发布的 MaterialMap 恢复，按毫米坐标显示实际几何和物性。原始轮廓对照、几何损失和拓扑结论可通过中间 JSON 与独立的数值诊断核对。材料掩膜用于数值采样。

region 与网格图按相同分区分组展示，不同 profile 分开展示，避免跨层投影重叠掩盖独立边界。

| 产物 | 用途 |
| --- | --- |
| `heatmaps/*.png` | 查看各层实际单元的 kx、ky、kz |
| `regions/regions.png`、`.svg` | 查看 MaterialMap 中的二维 region 边界 |
| `grids/cartesian-grid.png`、`.svg` | 查看局部单元、region 边界和不共形接缝 |
| `index.html` | 浏览本轮图表 |

热图横向排列 kx、ky、kz，使用毫米轴、等比例和线性 viridis 色标。默认使用该层三方向联合色域，`--heatmap-shared-scale` 改用层间共同范围，`--heatmap-grid` 叠加实际单元边界。面板宽默认 1440 像素，可用 `--heatmap-panel-width` 调整。无单元处显示灰色，未知 Z 明确标识。

可用 `--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 关闭相应输出。图表失败时的产物状态见[输出与发布](../output-publication.md)。
