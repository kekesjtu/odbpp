# 中间 JSON 接口

本文说明解析器交付的字段和物理语义。

解析器使用从 `contracts/intermediate.py` 同步到包内的 Pydantic 模型。Rust 等效器使用 Serde 和原生校验读取同一契约。
项目始终只有一套当前格式；不保存或比较 schema/generator 版本，不维护旧结构兼容分支。
格式变化时同步调整全部调用方并重新导出案例。结构不完整、类型错误和必要语义错误仍明确报错。

文件按缩进逐行写出，但坐标对在同一行内紧凑排列：案例规模可达百万级坐标，逐数字换行只放大体积，不服务阅读。读取一律使用 JSON 解析器。

`docs/interfaces/intermediate.schema` 是 UTF-8 JSON Schema 文本，由同一模型生成；其中 `$schema`
声明 JSON Schema 标准语法，不是业务版本。重新生成：

```powershell
uv run python tools/sync_contract.py
uv run python tools/sync_contract.py --check
```

## 数据流与职责

单位解释和极性规整由解析器负责。入口验证有限数值、点维度、显式岛孔、Z 起止、唯一标识、轮廓归属、铜层跨度及必要的厚度来源。近似误差、连通性和孔洞保留在等效器的矢量几何验收中检查。

## 唯一数据与字段

| 位置 | 内容 |
|---|---|
| 根 `units` | 固定 `mm`；不允许把 inch 几何交给等效器 |
| 根 `resolution_mm` | 输出坐标规整网格，默认 0.0001 mm；与材料光栅无关 |
| 根 `source` | 输入路径、job root、输入单位、产品名称及 ODB++ 版本；手工合成数据可省略 |
| 根 `steps` | 恰好一个单板 step，不支持矩阵拼板和嵌套引用 |
| step `profile_id` | `profiles` 中整板实体范围的引用 |
| step `profiles` | `{id, layers, polygons}`。按规整后几何去重的轮廓集合，包含继承的 step 孔；`layers` 列出所属 BOARD 层名称 |
| step `layers` | 按 matrix row 排列的 BOARD 层目录；唯一存储层类型、上下文、Z、跨度、厚度来源与图案 |
| layer `row` | 必填正整数，同一 step 内唯一；matrix 层序的权威来源，钻孔覆盖按端点铜层和介质的 row 判断 |
| layer `geometry_status` | `ready` 成功（允许空图案）、`failed` 失败、`omitted` 被筛选等原因未导出 |
| layer `geometry_error` | 失败或域不可用的原因；失败层必须提供，不静默继承 step 域 |
| layer `polygons` | 正向实体图案；岛和孔用 `island` 明确区分，不携带可执行的 ODB++ 极性 |
| layer `z_mm` | `{start_mm, thickness_mm}`；实体物理层自身的绝对 Z；值可为 `null`，DRILL 不导出 |
| layer `start_layer/end_layer` | DRILL 起止铜层名称；引用同一 step 的 BOARD 铜层，按 row 自顶向下排列；其他层不保存端点 |

MISC 层不进入此接口，包括层目录、图案、轮廓归属与叠层信息。MISC 预览直接使用独立绘图指令，不从中间 JSON 恢复。

同几何只导出一份 profile，包括整板与层轮廓之间的重复。环起点、绕序、共线点和对象身份不决定几何是否重复。层归属只保存在 `profiles[].layers`，同一层最多归属一个 profile；不可用轮廓不分配归属。

有层轮廓时，解析器从层岛中扣除层孔与 step 孔的并集；缺少层轮廓时使用完整 step 实体范围。`profiles` 交付最终实体多边形，消费者直接使用层域，不重复叠加 step 孔或按 step 岛裁剪，也不要求层域包含于整板域。轮廓、实体范围与有效计算域的区别见[项目架构](../architecture.md#从轮廓到有效计算域)。

所有坐标使用输入 step 原点，不重新居中；XY 中 Y 向上；Z 从叠层顶面向下。
`polygons[].points` 为二维数组，末尾不重复首点；岛顺时针，孔逆时针，嵌套岛保持独立正向环。
几何消费者沿用原有正向实体规则，不能把同向重叠当作孔，不能用绘制顺序解释图案。

## 介质实体范围与图元

介质实体范围仅保存在 `profiles[].polygons` 中，包含轮廓定义的真实空洞。介质 `layer.polygons` 从空几何开始，按本层图元极性规整，不填充轮廓基底；无图元时输出空数组，`geometry_status` 仍为 `ready`。

跨层 DRILL 作为独立图案交付，不改写本层图案。介质预览从 profile 构造基底，应用本层图元并扣除相交钻孔，仅作用于绘图副本。等效器以 profile 和钻孔投影确定介质材料分布，见[材料采样](../decisions/pcbeq/material-sampling.md)。

## 厚度与钻孔

`z_mm` 用两个可为 `null` 的字段表达两个独立事实：`start_mm` 是本层顶面的绝对 Z
（自叠层顶面向下，mm），`thickness_mm` 是本层自身厚度。任一侧不是实测就写 `null`，
不写估算值，因此读到的数字一律是测量结果。

- `start_mm=null`：上方有结构层未声明厚度，累计位置不可信。
- `thickness_mm=null`：本层自身厚度未被格式声明。
- 两者都为 `null`：位置与厚度均不可知。

`0.0` 是合法实测值（真实零厚度），与 `null` 含义不同。任一侧未知时 MaterialMap
使用 `[null,null]`，仍交付完整 XY 网格与物性，不据此计算可信体积或质量；
等效器分别记录 `z.absolute_position_unknown` 与 `z.thickness_unknown` 非阻断错误。
厚度缺失不阻断等效；需要绝对 Z 时，以起点铜层 `start_mm` 与终点铜层
`start_mm + thickness_mm` 推导，不能生成独立实体厚度层。

钻孔保存投影多边形和 `start_layer/end_layer` 来描述几何与深度；端点名称来自 matrix 的 START_NAME/END_NAME，引用解析后的实际层名。层身份和解析状态仍保留。
端点必须引用两个不同的 BOARD 铜层，且起点 row 小于终点 row。覆盖条件为 `起点铜层.row < 介质层.row < 终点铜层.row`，不依赖层厚或绝对 Z。最外侧铜层之外的介质不被覆盖。
需要介质采样时，钻孔缺少拓扑跨度或无法定位端点铜层均明确报错。绝对 Z 仅用于三维尺寸；未知位置或重合的厚度不改变覆盖关系。
无有效域、失败/未导出的待等效层、介质采样所需钻孔跨度未知仍阻止生成正式结果。
铜层忽略 DRILL，仅铜层输入不要求钻孔跨度。
原始层极性、轮廓基底使用状态与图元/极性段计数仅保留在内部模型及解析统计中，
不写入中间 JSON。根节点也不导出 `diagnostics`，
解析诊断通过命令行和独立的 `diagnostics/run.json` 交付；中间 JSON 保留逐层 `geometry_status/geometry_error`。
消费者直接使用规整后的实体几何。
