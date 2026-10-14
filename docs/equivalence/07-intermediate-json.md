# 当前中间 JSON 接口

解析器导出与等效器导入共用 `src/odbpp/intermediate.py` 中的 Pydantic 模型。
项目始终只有一套当前格式；不保存或比较 schema/generator 版本，不维护旧结构兼容分支。
格式变化时同步调整全部调用方并重新导出案例。结构不完整、类型错误和必要语义错误仍明确报错。

`docs/intermediate.schema` 是 UTF-8 JSON Schema 文本，由同一模型生成；其中 `$schema`
声明 JSON Schema 标准语法，不是业务版本。重新生成：

```powershell
uv run --all-extras python -c "from odbpp.intermediate import write_schema; write_schema('docs/intermediate.schema')"
```

## 数据流与职责

ODB++ 文本解析、符号展开、极性与拓扑规整继续使用 dataclass。
导出边界 `step_to_document()` 构造 Pydantic 模型，`write_document()` 直接序列化；
保留 `step_to_json()` 作为返回普通 JSON 字典的便利 API。
文件导入 `read_document()` 使用 `model_validate_json()`；等效器也验证直接传入的字典。
验证后的接口容器转换为普通映射，复用坐标列表，随后构造原有 Ring/Region dataclass。
像素、扫描线、region、网格单元和物性循环不构造 Pydantic 对象。

单位解释、极性规整属于解析器；几何有效性、内接性、连通性和孔洞保留属于几何算法。
Pydantic 不代替这些算法，也不自动修复物理数据。入口验证包括有限数值、点维度、
显式岛/孔、Z 起止、唯一标识、域引用、铜层跨度以及英制铜重换算来源。

## 唯一数据与字段

| 位置 | 内容 |
|---|---|
| 根 `units` | 固定 `mm`；不允许把 inch 几何交给等效器 |
| 根 `resolution_mm` | 输出坐标规整网格，默认 0.0001 mm；与材料光栅无关 |
| 根 `source` | 输入路径、job root、输入单位、产品名称及 ODB++ 版本；手工合成数据可省略 |
| 根 `steps` | 恰好一个单板 step，不支持矩阵拼板和嵌套引用 |
| step `profile_domain_id` | 整板轮廓在 `domains` 中的引用；轮廓只存一次 |
| step `domains` | `{id, source, polygons}`；层级域与整板域均可被多层引用 |
| step `layers` | 按 matrix row 排列的完整层目录；唯一存储层类型、上下文、Z、跨度、厚度来源与图案 |
| layer `domain_id` | 已按 layer profile 优先规则确定的计算域；与铜图案不同 |
| layer `geometry_status` | `ready` 成功（允许空图案）、`failed` 失败、`omitted` 被筛选等原因未导出 |
| layer `geometry_error` | 失败或域不可用的原因；失败层必须提供，不静默继承 step 域 |
| layer `polygons` | 正向实体图案；岛和孔用 `island` 明确区分，不携带可执行的 ODB++ 极性 |
| layer `z_mm` | `{start_mm, end_mm, thickness_known, source}`；非实体层或未解算钻孔可缺省 |
| layer `thickness` | 本层厚度可信状态与原始物理量来源；不再保存第二份 mm 厚度 |
| layer `copper_index` | 铜层自顶向下的索引 |
| layer `copper_span` | 介质或钻孔跨越的两个铜层索引；不重复保存端点名称 |
| layer `form/form_source` | RIGID/FLEX 及其声明、子类型或规范默认来源 |
| layer `parse_info` | 可选解析审计：原始层极性、profile 基底、图元/极性段计数、已扣除钻孔和面积 |
| 根 `diagnostics` | 可选解析诊断；层失败状态不依赖此列表是否导出 |

去掉 `schema`、`generator`、`coordinate_system` 的常量说明、`summary`、独立 `profile`、
独立 `stackup`、面积/包围盒缓存、派生 `is_copper/is_flex`、层的重复 domain 来源、
原始属性副本与旧铜重/厚度数值副本。计算所需数据只有一份。

所有坐标使用输入 step 原点，不重新居中；XY 中 Y 向上；Z 从叠层顶面向下。
`polygons[].points` 为二维数组，末尾不重复首点；岛顺时针，孔逆时针，嵌套岛保持独立正向环。
几何消费者沿用原有正向实体规则，不能把同向重叠当作孔，不能用绘制顺序解释图案。

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
钻孔的 Z 是跨层范围，不能由它生成独立实体厚度层。
未知厚度下仍以 `copper_span` 确定钻孔拓扑，不能仅凭重合的估算 Z 视为贯穿全板。
无有效域、失败/未导出的待等效层、必要钻孔跨度未知仍阻止生成正式结果。
`parse_info.polarity` 仅用于核对原始输入，等效器不得重新解释它。

## 使用与验证

```python
from odbpp import load_job, build_step_preview, step_to_document, write_document, read_document

job = load_job("cases/kicad-kitchen-sink")
board = step_to_document(job, build_step_preview(job))
write_document(board, "build/board.json")
board = read_document("build/board.json")
# equivalence_from_document(board, options) 或传入合法当前格式的普通字典。
```

JSON Schema 可描述类型和基本范围；Python 自定义校验器中的跨字段关系不会全部展开成
JSON Schema，以上语义及 tests/test_intermediate.py 同时维护。
Pydantic 模型不用于阶段内任意可变状态，也不使用跳过验证的构造来接收外部文件。

真实案例前后对照工具为 `tools/validate_intermediate.py`，报告见
`intermediate-validation.json`。独立进程比较原始浮点物性、CSV、掩膜、region/网格坐标，
并分开记录 export/import 时间、JSON 大小与峰值内存。全案例最终结果使用 sync_out 更新。
