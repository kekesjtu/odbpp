# 逐层轮廓与绝对叠层

## 轮廓与实体范围

层实体范围遵循下列规则，依据 [ODB++ 层轮廓规范](../../research/odbpp_format/profiles.md) p.181–183。

- 有层轮廓（layer profile）：从层岛中扣除层孔与 step 孔的并集。step 孔完全落在层岛内或与层岛相交时，均参与扣除；相交的孔可使最终层岛缩小、分裂或消失。
- 无层轮廓：以整板轮廓（step profile）作为默认值，层实体范围为 step 岛扣除 step 孔。
- 层轮廓存在但无法解析：报错并终止本轮导出，不回退到整板轮廓；中间 JSON 因此总是描述一块完整的板。

```text
有 layer profile：层实体范围 = layer 岛 − (layer 孔 ∪ step 孔)
无 layer profile：层实体范围 = step 岛 − step 孔
```

step 岛仅在缺省层轮廓时提供替代范围。层岛不要求包含于 step 岛，也不按 step 岛裁剪；层轮廓伸出整板轮廓属于允许的几何。孔的重叠部分按并集扣除，只计一次。

解析器将 BOARD 层的最终实体范围存入中间 JSON 的 `profiles`，按规整后的几何去重，所属层名单记录在 profile 根下，JSON 与预览中的层域使用相同规则。等效器直接以该完整实体范围作为层有效计算域，并报告近似模型的漏失与新增面积。术语和分工见[项目架构](../../architecture.md)，profile 去重与独立分区见[几何规划](../pcbeq/geometry-planning.md)。

轮廓孔定义真实空洞，与跨层 DRILL 图案分开处理。介质实体范围保存在 `profiles`，介质 `layer.polygons` 只保存本层图元规整结果，不填充轮廓基底。预览从层实体范围构造介质基底，应用本层图元并扣除相交钻孔，绘图结果不写回中间 JSON。

## 叠层与厚度决策

Z 使用毫米，自叠层顶面向下。matrix row 给出层次，钻孔以 `start_layer/end_layer` 直接引用起止 BOARD 铜层。介质只需自身 row，不保存相邻铜层关系。钻孔覆盖与预览扣孔仅比较介质 row 和端点铜层 row，不依赖层厚或绝对 Z。
板内实体层给出绝对 Z 起点与本层厚度。厚度取自输入声明或由其换算，已知的层顶位置由声明厚度累计得到，未知项写 `null`。

| 来源 | 解释 |
| --- | --- |
| `.layer_dielectric` | 当前属性文件单位下的层厚；规范用于 DIELECTRIC、SOLDERMASK、SOLDER PASTE 等非铜层 |
| MM `.copper_weight` | µm 铜厚度 |
| INCH `.copper_weight` | oz/ft² 面密度，按默认铜密度 8.96 g/cm³ 换算厚度 |

铜层优先使用 `.copper_weight`。缺少可读取的铜重时，解析器可采用该层声明的 `.layer_dielectric`，并对这一格式偏差发出告警。

`start_mm` 是本层顶面位置，`thickness_mm` 是本层自身厚度；`null` 分别表示累计位置不可信与厚度未被格式声明。
两个值独立，本层厚度可以已知而累计位置未知。`0.0` 表示已知的零值。
铜层、介质层或阻焊层的厚度未知时，后续位置也标记为未知。装饰层缺少厚度只标记该层的厚度未知。
任一项未知时，MaterialMap 的 Z 区间写为 `[null, null]`。

DRILL 作为跨层图案交付投影和起止铜层名称。字段定义见[中间 JSON](../../interfaces/intermediate-json.md)，覆盖规则见[材料采样](../pcbeq/material-sampling.md)。
