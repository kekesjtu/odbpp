# pcbeq 可视化决策

`pcbeq-rust` 的 Python 绘图只读取正式 MaterialMap：从 region 范围与网格数恢复模型几何、局部网格和物性热图，不读取中间 JSON，也不依赖计算诊断。材料预览、原始 profile 叠加及几何损失不能从 MaterialMap 推断并绘制。

## 输出含义

| 产物 | 数据来源 | 用途 |
| --- | --- | --- |
| `pcbeq/heatmaps/*.png` | 正式 MaterialMap JSON | 每层 kx / ky / kz 的实际单元热图 |
| `pcbeq/regions/*` | 实际内接矩形集合 | region 边界、面积、编号、尺寸和几何损失 |
| `pcbeq/grids/*` | 各 region 的范围、网格数与实体单元 | 局部均匀划分、不共形接缝与尺寸检查 |

各图分别服务于不同检查；几何误差使用矢量域验证，材料误差使用实际数值采样收敛验证。

## 物性热图

每层一张 PNG，三面板横向排列 kx、ky、kz，共用线性 viridis 色标、毫米轴和等比例显示。
默认色域为该层三方向联合范围；`--heatmap-shared-scale` 使用层间共同范围，`--heatmap-grid` 绘制实际单元边界。
均匀层显示唯一值，无单元区域显示灰色与图例，Z 为 null 时标记未知。
面板宽默认 1440 像素，由 `--heatmap-panel-width` 控制。
绘图以实际毫米矩形构成 PolyCollection，显示像素尺寸不改变材料数据或网格坐标。

## region 与局部网格

region 图按 MaterialMap 的实际矩形绘制边界和编号；几何误差与拓扑检查记录在 Rust 数值诊断中。
局部网格图显示每个 region 内的实际单元和 region 边界；网格线仅绘于所属 region 内，允许接缝不共形，不绘制板外轴线延伸。
逐层实体存在性和五项物性以 MaterialMap 中的完整三维 region 为准。

## 选项、文件名与失败

默认生成热图、region、grid 和图例页，可分别用 `--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 控制。
层名转为文件系统安全名称，大小写冲突和后缀冲突分配空闲名称，图例保留原层名。
数值与所请求的图片按 [发布决策](../output-publication.md) 分别事务发布。
