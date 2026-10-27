# 预览、材料掩膜与几何诊断

## 输出含义

| 产物 | 数据来源 | 用途 |
| --- | --- | --- |
| `pcbkit/preview.svg`、`preview.html` | Parser 最终规整的矢量 | 图层、走线、孔、计算域与叠层核对 |
| `pcbeq/masks/*.png` | 实际材料采样掩膜 | 铜 / 基底 / 域外及钻孔覆盖核对 |
| `pcbeq/heatmaps/*.png` | 正式 MaterialMap JSON | 每层 kx / ky / kz 的实际单元热图 |
| `pcbeq/regions/*` | 本轮内接矩形集合 | region 边界、面积、编号、尺寸和几何损失 |
| `pcbeq/grids/*` | 最终全局轴线与实体单元 | 强制边界、传播细分与网格尺寸检查 |

各图分别服务于不同检查；几何误差使用矢量域验证，材料误差使用实际数值采样收敛验证。

## 材料掩膜

黑色 0 表示材料 1，白色 255 表示域内材料 2，灰色 160 表示该层域外。
DRILL 只在相交介质层内为黑色，铜层掩膜完全由自身图案形成。
像素行方向与 Y 向上的板坐标一致：图像上方对应 Y 最大处。

自动显示按两轴物理间距保持板的长宽比，最近邻重采样，目标长边约 1400 像素，受 4,000,000 显示像素预算约束。
显示图可压缩小特征，采样分辨率以运行诊断中的 Px/Py 为准。
`--raster-scale N` 强制各向同性整数缩放，非等物理像素时可能改变显示比例。
掩膜逐层生成和释放。

## 物性热图

每层一张 PNG，三面板横向排列 kx、ky、kz，共用线性 viridis 色标、毫米轴和等比例显示。
默认色域为该层三方向联合范围；`--heatmap-shared-scale` 使用层间共同范围，`--heatmap-grid` 绘制实际单元边界。
均匀层显示唯一值，无单元区域显示灰色与图例，Z 为 null 时标记未知。
面板宽默认 1440 像素，由 `--heatmap-panel-width` 控制。
绘图以实际毫米矩形构成 PolyCollection，显示像素尺寸不改变材料数据或网格坐标。

## region 与全局网格

region 图对照原始轮廓，逐矩形显示边界、面积对数色标、编号与尺寸表，并统计短边、损失、孔洞和连接保护结果。
全局网格图显示实际内域单元、域外轴线延伸、region 边界与原始 step profile。
逐层实体存在性和五项物性以 MaterialMap 中的完整三维 region 为准。

## 选项、文件名与失败

默认生成掩膜、热图、region、grid 和图例页，可分别用 `--no-raster-png`、`--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 控制。
层名转为文件系统安全名称，大小写冲突和后缀冲突分配空闲名称，图例保留原层名。
所请求的图片与数值结果按 [发布决策](decisions/output-publication.md) 一起完成后发布。

实现位于 `pcbeq.png/visualize/plotting` 与 `pcbkit.svg`。命令见 [开发说明](development.md)，验证范围见 [验证数据](validation/README.md)。
