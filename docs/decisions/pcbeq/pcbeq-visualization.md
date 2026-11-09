# pcbeq 工程图表

两版热图都读取正式 MaterialMap。Python 参考版还可利用输入轮廓和计算诊断做几何对照，并单独采样整板材料预览。Rust 版的所有图表均由 MaterialMap 恢复。

| 产物 | Python 版 | Rust 版 |
| --- | --- | --- |
| `masks/*.png` | 独立的整板材料预览，可核对铜、基底、域外和钻孔覆盖 | 无 |
| `heatmaps/*.png` | 每层 kx、ky、kz 单元热图 | 同左 |
| `regions/regions.png`、`.svg` | 对照原始轮廓，显示 region 边界、面积色标和编号 | 显示 MaterialMap 中的实际 region 边界 |
| `regions/regions.csv`、`regions.stats.json` | 编号、尺寸及面积和拓扑等诊断 | 无 |
| `grids/cartesian-grid.png`、`.svg` | 显示局部单元、region 边界与原始整板轮廓 | 显示局部单元和 region 边界 |
| `grids/cartesian-grid.stats.json` | 网格诊断 | 无 |
| `index.html` | 图表索引 | 图表索引 |

局部网格图只在各 region 内画网格线，可核对不共形接缝。几何误差按矢量域核对，材料采样误差按实际数值分辨率核对。

## 材料掩膜

黑色 0 表示材料 1，白色 255 表示域内材料 2，灰色 160 表示该层有效计算域之外。图像上方对应板坐标 Y 的最大值。铜与钻孔覆盖的判定见[材料采样](material-sampling.md)。

自动显示采用最近邻重采样保持毫米比例，目标长边约 1400 像素，受 4,000,000 显示像素预算约束。`--raster-scale N` 使用整数缩放，像素物理间距不等时可能改变显示比例。预览分辨率和各 region 的数值光栅独立。

## 物性热图

每层 PNG 横向排列 kx、ky、kz，使用毫米轴、等比例和线性 viridis 色标。默认使用该层三方向联合色域，`--heatmap-shared-scale` 改用层间共同范围，`--heatmap-grid` 叠加实际单元边界。面板宽默认 1440 像素，可用 `--heatmap-panel-width` 调整。无单元处显示灰色，未知 Z 明确标识。

## 图表开关

两版均可用 `--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 关闭相应输出。Python 版另支持 `--no-raster-png`。图表失败时的产物状态见[输出与发布](../output-publication.md)。
