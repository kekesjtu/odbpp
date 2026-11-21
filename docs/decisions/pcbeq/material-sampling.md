# 独立材料采样与钻孔覆盖

## 决策与范围

各实际 region 使用独立光栅，像素中心与网格共享毫米物理坐标系，允许 X/Y 像素间距不同。各层的有效计算域直接取中间 JSON 交付的完整层实体范围，已按[层轮廓规则](../pcbkit/layer-profiles-and-stackup.md#轮廓与实体范围)扣除轮廓孔。扫描线在区间或掩膜层组合计算域、铜图案和相交钻孔，不额外叠加整板实体范围掩膜。

```text
effective_region = layer_extent
copper_black = effective_region AND copper_pattern
dielectric_black = effective_region AND overlapping_drills
white = region AND NOT black
```

介质起始铜图案为空。所有介质内 DRILL 投影统一作为材料 1 实心铜。
铜层只使用自身图案。铜图案和钻孔投影均受原始有效计算域限制。层域伸出整板轮廓的部分按相同材料规则采样。近似 region 中超出原域的部分，包括近似孔边界内的越界像素，统一归为本层基底材料；完整模型单元不按覆盖率缩减体积。
DOCUMENT、COMPONENT 等非物理层以及尚无适用材料模型的层不参与等效与 profile 统计。

## 深度与图案语义

DRILL 端点来自 matrix 的 START_NAME / END_NAME，JSON 的 `start_layer/end_layer` 直接引用实际 BOARD 铜层名，并按 row 自顶向下排列。
通过层名找到端点铜层后，覆盖只按 matrix 的 `row` 判断：`起点铜层.row < 介质层.row < 终点铜层.row`。通孔、盲孔、埋孔使用相同规则，最外侧铜层之外的介质不被覆盖。
层序必须明确且唯一，端点须为两个不同铜层；必要端点无法解析时报告可定位的错误。
仅铜层输入无需解析钻孔跨度。

介质无需保存相邻铜层关系。层厚未知、厚度为零或绝对 Z 重合均不改变钻孔覆盖。绝对 Z 用于表达三维尺寸及其可信状态。
圆、长圆、方形、槽形和自定义孔形由 Parser 展开成投影多边形。采样直接使用该几何。
每个规整 Region 按岛孔嵌套求内部区间，不同 DRILL 集合按 OR 组合，重叠只计一次。

当前模型对金属化与非金属化 DRILL 统一使用实心铜，对孔壁、填孔及背钻采用相同投影近似。
ROUT 的真实挖空应由整板或层轮廓表达。同一钻孔层共享跨度，Z 内变化的孔径不在二维贯通模型中表达。

## 分辨率与容量

每个物性单元沿 X、Y 各均匀分成 N 份，在 N×N 个像素的中心判定材料。参数 `--pixels-per-cell-axis N` 取正整数，默认 9。单元宽度为 w、高度为 h 时，像素间距分别为 w/N 和 h/N。相对单元左下角，第 i 列、第 j 行的采样点位于 `((i+0.5)w/N, (j+0.5)h/N)`，其中 i、j 从 0 到 N−1。

每个 region 的局部光栅宽、高分别为该 region 的 X、Y 分格数乘以 N。短边及伸出整板轮廓的单元采用相同规则。每个 region 最多容纳 120,000,000 个像素，超限时报告 region、分格数及请求的 N。细走线、小孔的捕获和物性收敛可通过固定网格、提高 N 对照验证。

## 扫描与统计

数值掩膜按 region 生成、积分并释放。正式诊断按有效 profile 汇总 region 与物性网格尺寸，逐 region 光栅、像素规模和掩膜仅用于内部计算。

材料扫描按完整轮廓筛选几何，保留包围采样窗口的外环和岛孔关系。两种[面内热导模型](material-model.md#等效物性)共用同一像素掩膜和物性网格。2DVTN 使用光栅物理间距定义相邻像素的热阻。工程图表从 [MaterialMap](../../interfaces/materialmap.md) 恢复。
