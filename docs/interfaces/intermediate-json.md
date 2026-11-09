# 中间 JSON 接口

本文说明解析器交付的字段和物理语义。

两版解析器与 Python 等效器使用从 `contracts/intermediate.py` 同步到各自包内的 Pydantic 模型。Rust 等效器使用 Serde 和原生校验读取同一契约。
项目始终只有一套当前格式；不保存或比较 schema/generator 版本，不维护旧结构兼容分支。
格式变化时同步调整全部调用方并重新导出案例。结构不完整、类型错误和必要语义错误仍明确报错。

`docs/interfaces/intermediate.schema` 是 UTF-8 JSON Schema 文本，由同一模型生成；其中 `$schema`
声明 JSON Schema 标准语法，不是业务版本。重新生成：

```powershell
uv run python tools/sync_contract.py
uv run python tools/sync_contract.py --check
```

## 数据流与职责

单位解释和极性规整由解析器负责。入口验证有限数值、点维度、显式岛孔、Z 起止、唯一标识、域引用、铜层跨度及必要的厚度来源。内接性、连通性和孔洞保留在等效器的矢量几何验收中检查。

## 唯一数据与字段

| 位置 | 内容 |
|---|---|
| 根 `units` | 固定 `mm`；不允许把 inch 几何交给等效器 |
| 根 `resolution_mm` | 输出坐标规整网格，默认 0.0001 mm；与材料光栅无关 |
| 根 `source` | 输入路径、job root、输入单位、产品名称及 ODB++ 版本；手工合成数据可省略 |
| 根 `steps` | 恰好一个单板 step，不支持矩阵拼板和嵌套引用 |
| step `profile_domain_id` | `domains` 中整板实体范围的引用 |
| step `domains` | `{id, source, polygons}`。保存整板或层轮廓围成的实体范围，可被多层引用 |
| step `layers` | 按 matrix row 排列的完整层目录；唯一存储层类型、上下文、Z、跨度、厚度来源与图案 |
| layer `domain_id` | 层实体范围的引用。等效器再与整板实体范围求交得到层有效计算域 |
| layer `geometry_status` | `ready` 成功（允许空图案）、`failed` 失败、`omitted` 被筛选等原因未导出 |
| layer `geometry_error` | 失败或域不可用的原因；失败层必须提供，不静默继承 step 域 |
| layer `polygons` | 正向实体图案；岛和孔用 `island` 明确区分，不携带可执行的 ODB++ 极性 |
| layer `z_mm` | `{start_mm, end_mm, thickness_known, source}`；实体物理层自身的位置；DRILL 不导出 |
| layer `thickness` | 本层厚度可信状态与原始物理量来源；保留原始量追溯；数值厚度由 Z 差得到 |
| layer `copper_index` | 铜层自顶向下的索引 |
| layer `copper_span` | 介质或钻孔跨越的两个铜层索引；不重复保存端点名称 |
| layer `form/form_source` | RIGID/FLEX 及其声明、子类型或规范默认来源 |

轮廓、实体范围与有效计算域的区别见[项目架构](../architecture.md#从轮廓到有效计算域)。

所有坐标使用输入 step 原点，不重新居中；XY 中 Y 向上；Z 从叠层顶面向下。
`polygons[].points` 为二维数组，末尾不重复首点；岛顺时针，孔逆时针，嵌套岛保持独立正向环。
几何消费者沿用原有正向实体规则，不能把同向重叠当作孔，不能用绘制顺序解释图案。

## 未扣钻孔的介质几何

`layer.polygons` 保存按本层图元极性和基底规整后的图案，保留轮廓定义的真实空洞。跨层 DRILL 作为独立图案交付，不从介质基底扣除，也不改写铜图案。预览中的扣孔仅作用于绘图副本，等效时的钻孔覆盖见[材料采样](../decisions/pcbeq/material-sampling.md)。

## 厚度与钻孔

数值建模厚度仅由 `z_mm.end_mm - z_mm.start_mm` 得到。
`thickness.known` 表示本层是否有有效厚度来源；`z_mm.thickness_known` 沿用绝对叠层的
联合可信状态，若上方结构层厚度未知，即使本层厚度明确，也可能为 false。
因此两个布尔值表达不同问题；等效结果继续记录联合状态，避免把估算位置当实测。

`thickness` 中 `input_value/kind/unit` 是原始量追溯，属于统一 mm 几何的显式例外。
介质输入为 `kind=thickness`，单位 mm/inch；公制铜重为 `kind=thickness_um, unit=um`；
英制为 `kind=areal_density_oz_ft2, unit=oz/ft2`，必须带
`thickness_source=assumed_density` 与 `assumed_density_g_cm3=8.96`。
原始值不会由等效器再次换算，也不会覆盖 Z。

厚度缺失时允许零厚度 best-effort Z，并明确 false；不以严格正厚度约束拒绝这种合法解析状态。
钻孔仅保存投影多边形和 `copper_span` 来描述几何与深度；层身份和解析状态仍保留。
需要绝对 Z 时，以起点铜层 `z_mm.start_mm` 和终点铜层 `z_mm.end_mm` 推导，不能生成独立实体厚度层。
未知厚度下仍以 `copper_span` 确定钻孔拓扑，不能仅凭重合的估算 Z 视为贯穿全板。
跨度端点须引用层目录中的实际铜层索引；需要介质采样时，无拓扑跨度或端点铜层缺少 Z 均明确报错。
无有效域、失败/未导出的待等效层、介质采样所需钻孔跨度未知仍阻止生成正式结果。
铜层忽略 DRILL，仅铜层输入不要求钻孔跨度。
原始层极性、轮廓基底使用状态与图元/极性段计数仅保留在内部模型及解析统计中，
不写入中间 JSON。根节点也不导出 `diagnostics`，
解析诊断通过命令行和独立的 `diagnostics/run.json` 交付；中间 JSON 保留逐层 `geometry_status/geometry_error`。
消费者直接使用规整后的实体几何。
