# 独立材料采样与钻孔覆盖

## 决策与范围

材料分支独立读取中间 JSON，在原始 step profile 包围盒内建立 Px×Py 光栅，允许 X/Y 像素物理间距不同。
计算域、铜图案和相交钻孔分别扫描线处理，在区间或掩膜层组合：

```text
domain = step_profile AND layer_domain
copper_black = domain AND copper_pattern
dielectric_black = domain AND overlapping_drills
white = domain AND NOT black
```

介质起始铜图案为空。所有介质内 DRILL 投影统一作为材料 1 实心铜，记录 `drill_model=solid_copper`、`drill_scope=dielectric_only`。
铜层只使用自身图案；profile 的真实孔洞是域外几何，钻孔投影受 domain 限制。
DOCUMENT、COMPONENT 等非物理层以及尚无适用材料模型的层明确记录跳过原因。

## 深度与图案语义

DRILL 端点来自 matrix 的 START_NAME / END_NAME，JSON 保存递增铜层索引 `copper_span`。
介质与钻孔优先按铜层拓扑区间正长度相交判定，通孔、盲孔、埋孔使用相同规则。
需要 Z 时从起点铜层 start_mm 和终点铜层 end_mm 推导；必要跨度或端点 Z 不可用即报告错误。
仅铜层输入无需解析钻孔跨度。

介质缺少拓扑跨度时用推导 Z 判断，非零厚度采用半开区间，零厚度按所在平面是否到达判断；未知厚度回退发出 `equiv.drill_z_fallback`。
圆、长圆、方形、槽形和自定义孔形由 Parser 展开成投影多边形。采样直接使用该几何。
每个规整 Region 按岛孔嵌套求内部区间，不同 DRILL 集合按 OR 组合，重叠只计一次。

当前模型对金属化与非金属化 DRILL 统一使用实心铜，对孔壁、填孔及背钻采用相同投影近似。
ROUT 的真实挖空应在 profile / domain 中表达。同一钻孔层共享跨度，Z 内变化的孔径不在二维贯通模型中表达。

## 分辨率与容量

默认光栅长边至少 4096 像素，起始尺寸保留板的物理长宽比。
几何网格确定后，自动光栅按实际最短区间加密：档位模式每轴至少 8 个采样中心，专家至少 1 个。
自动计算预留额外样本以应对像素相位，最多 120,000,000 像素。用户显式 Px/Py 不足则报错并提示加密光栅或调整网格上限。

像素中心按索引与半开区间归属单元，像素边界无需对齐网格。每个输出单元都必须有有效采样且所有采样点位于本层域内。
容量阈值保证单元可采样；小走线、小孔是否捕获及物性是否收敛，仍由实际分辨率对照验证。
材料采样使用二值像素中心，显示 PNG 分辨率独立于数值光栅。

## 依据与实现对照

复杂材料图案通过扫描线区间逻辑组合，简单 profile 的几何布尔检查由几何分支负责。
逐层驻留字节掩膜，行前缀和与列累计复用同一全局行带的统计；kx / ky 保留逐行 / 逐列公式。
缓存以本次几何、跨度和光栅配置区分，材料缓存与像素索引只服务采样。
实现位于 `pcbeq.equiv/raster/mesh`，公式见 [串并联推导](../derivation/串并联PCB等效热模型推导.md)。
