# 逐层轮廓与绝对叠层

## 轮廓与实体范围

依据 [ODB++Design Format Specification，Release 8.1 Update 4](../../reference/ODB++_Format_User_Guide.pdf#page=182) p.181–183 的层轮廓定义，层实体范围遵循以下规则：

- 有层轮廓（layer profile）：从层岛中扣除层孔与 step 孔的并集。step 孔完全落在层岛内或与层岛相交时，均参与扣除；相交的孔可使最终层岛缩小、分裂或消失。
- 无层轮廓：以整板轮廓（step profile）作为默认值，层实体范围为 step 岛扣除 step 孔。
- 层轮廓存在但无法解析：报告该层失败。

```text
有 layer profile：层实体范围 = layer 岛 − (layer 孔 ∪ step 孔)
无 layer profile：层实体范围 = step 岛 − step 孔
```

step 岛仅在缺省层轮廓时提供替代范围。层岛不要求包含于 step 岛，也不按 step 岛裁剪；层轮廓伸出整板轮廓属于允许的几何。孔的重叠部分按并集扣除，只计一次。

解析器将最终层实体范围存入中间 JSON，JSON 与预览中的层域使用相同规则。等效器直接以该完整实体范围作为层有效计算域，并报告近似模型的漏失与新增面积。术语和分工见[项目架构](../../architecture.md)，profile 去重与独立分区见[几何规划](../pcbeq/geometry-planning.md)。

轮廓孔定义真实空洞，与跨层 DRILL 图案分开处理。介质 JSON 保留已扣轮廓孔、未扣 DRILL 的基底几何，预览在绘图副本中扣除相交钻孔。

## 叠层与厚度决策

Z 使用毫米，自叠层顶面向下。matrix row 给出层次，钻孔以 `start_layer/end_layer` 直接引用起止 BOARD 铜层。介质只需自身 row，不保存相邻铜层关系。钻孔覆盖与预览扣孔仅比较介质 row 和端点铜层 row，不依赖层厚或绝对 Z。
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

DRILL 作为跨层图案交付投影和起止铜层名称。字段定义见[中间 JSON](../../interfaces/intermediate-json.md)，覆盖规则见[材料采样](../pcbeq/material-sampling.md)。
