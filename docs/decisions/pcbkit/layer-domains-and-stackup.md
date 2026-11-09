# 逐层轮廓与绝对叠层

## 轮廓选择

解析器优先使用有效的层轮廓（layer profile），缺失时用整板轮廓（step profile）。层轮廓存在但无法解析时，报告该层失败。解析器将轮廓围成的实体范围存入中间 JSON。

等效器取层实体范围与整板实体范围的交集作为层有效计算域。两版均报告有效域内的模型损失，Python 参考版还分别报告输入层实体范围的面积和越出整板的面积。术语和分工见[项目架构](../../architecture.md)，共享分区见[几何规划](../pcbeq/geometry-planning.md)。

介质 JSON 保留未扣 DRILL 的基底几何，预览在绘图副本中扣除相交钻孔。

## 叠层与厚度决策

Z 使用毫米，自叠层顶面向下。matrix 行序给出层次，铜层具有自上而下的 `copper_index`，介质和钻孔用 `copper_span` 表示铜层跨度。
板内实体层给出 best-effort 的绝对 Z 起止与来源。数值厚度由 Z 差得到。

| 来源 | 解释 |
| --- | --- |
| `.layer_dielectric` | 当前属性文件单位下的层厚；规范用于 DIELECTRIC、SOLDERMASK、SOLDER PASTE 等非铜层 |
| MM `.copper_weight` | µm 铜厚度 |
| INCH `.copper_weight` | oz/ft² 面密度，按默认铜密度 8.96 g/cm³ 换算厚度 |

原始量使用 `input_value/kind/unit` 追溯；英制铜重记录 `thickness_source=assumed_density` 和所用密度。
非规范厚度来源通过 `spec_deviation` 标识。

`thickness.known` 表示本层有可靠厚度来源；`z_mm.thickness_known` 同时表示本层厚度与累计位置可信。
结构层厚度未知时，后续位置携带未知状态；装饰层厚度未知时标记自身未知，铜面位置的累计可信性独立维护。
MaterialMap 对联合可信状态为 false 的两个 Z 端点使用 null；中间 JSON 与运行诊断保留估算值。

DRILL 作为跨层图案交付投影和 `copper_span`。字段定义见[中间 JSON](../../interfaces/intermediate-json.md)，覆盖规则见[材料采样](../pcbeq/material-sampling.md)。
