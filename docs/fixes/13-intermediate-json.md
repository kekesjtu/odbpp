# 中间 JSON 精简与 Pydantic 边界（2026-10-14）

按用户确认的单一全链路格式实施，不做 schema/generator 版本协商，不维护历史结构迁移。
解析器导出与等效器导入共用 Pydantic 模型，内部图元、几何、region、网格和采样仍为
dataclass/元组/数组。接口说明见 [07](../equivalence/07-intermediate-json.md)，
文本定义见 [intermediate.schema](../intermediate.schema)。

## 精简与行为

- 整板轮廓仅保存在 domains，step 使用 profile_domain_id 引用。
- 删除重复 stackup；layers 为完整唯一目录，成功、失败和未导出状态明确，成功空层仍为 ready。
- 删除 schema/generator、summary、面积/包围盒缓存、派生布尔值、重复 domain 来源和原始属性副本。
- 保留绝对 Z；数值厚度只由 Z 差推导，thickness 集中记录原始物理量及换算来源。
- 分开记录本层厚度已知与累计绝对叠层联合可信状态；英制铜重保留 kind/unit、密度假设与来源。
- copper_span 只保存解析后的铜层索引对，引用不存在铜层、倒置区间和未知钻孔跨度明确报错。
- 原始层极性与钻孔扣除计数移到可选 parse_info，等效器不会重新执行极性解释。

导出逐层验证，避免完整的无类型坐标树与第二份 Pydantic 树同时驻留。
坐标对使用紧凑元组，没有逐顶点 Pydantic 对象。文件读取直接使用 model_validate_json，
导出直接使用 model_dump_json；内部映射复用坐标序列后进入原有几何结构。

## 四个真实案例对照

基准 `bbc77fa`；接口与减少副本实现 `b8d39b9`。之后补充未知 best-effort 钻孔 Z 的拒绝路径，
不影响以下跨度明确的真实案例。环境使用 uv 锁定依赖，Pydantic 2.14.1。
通过 `tools/validate_intermediate.py --baseline-ref bbc77fa --repeat 1` 在顺序启动的独立进程中测量。
不包含热图渲染；进程峰值包含验证工具自身的几何/结果散列。每案例一次，属于观察值，
不能当作稳定性能承诺；小案例尤其受启动后的文件缓存和系统负载影响。

| 案例 | JSON 字节：前 → 后 | 导出秒：前 → 后 | 导入秒：前 → 后 | 进程峰值 MiB：前 → 后 |
|---|---:|---:|---:|---:|
| minimal-test | 46855 → 44579 | 0.0031 → 0.0015 | 0.0083 → 0.0076 | 69.0 → 76.7 |
| kitchen-sink | 741160 → 710203 | 0.0387 → 0.0169 | 0.0106 → 0.0137 | 70.2 → 79.9 |
| video | 38102143 → 38082349 | 3.5566 → 1.4292 | 0.3882 → 0.5621 | 284.0 → 399.2 |
| rigidflex | 95643809 → 95563137 | 11.8757 → 6.3553 | 1.0013 → 2.0453 | 1590.3 → 1594.6 |

四案例的以下数据完全一致：逐层规整后几何与 domain、绝对 Z、region/全局轴线/网格坐标、
每个等效单元未舍入的铜占比及五项物性、最终 CSV 字节、逐层原始掩膜字节和光栅配置。
原始散列、计时及每层掩膜记录见 [intermediate-validation.json](../equivalence/intermediate-validation.json)。

精简主要消除接口中的重复语义；大案例由几何坐标主导，JSON 字节减少幅度很小。
Pydantic 带来入口结构和有限数值检查，也增加大文件导入成本。video 仍有明显额外峰值内存，
因此没有把这次改造描述为全流程内存优化，也没有把数值循环迁入 Pydantic。

## 回归范围

边界测试覆盖字段缺失、未知字段、非有限坐标、点维度、岛/孔语义、重复标识、域引用、
铜层引用、Z 倒置与未知零厚度、失败层原因、英制铜密度来源、无轮廓的诊断 round-trip，
以及 JSON 文件 round-trip 后的几何和物性一致性。
现有合成公式、钻孔、刚挠域、内接拓扑、档位/尺寸约束、采样容量与分辨率独立性测试继续保留。

最终全量回归 **761 passed in 266.05 s**。随后为 CLI 增加导出接口校验失败的
错误处理（返回 2，显示字段诊断，不产生无效 JSON/CSV）；相关接口、CLI、SVG 测试
**125 passed in 42.20 s**，包含两个新增非有限铜重错误路径。

`uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid`
刷新全部 **19 个案例**，包括原有未跟踪的 designodb-rigidflex 解压目录及其 tgz，
保留输入目录原样。每个 case 的 JSON、SVG/HTML、CSV/meta、region/网格诊断图、
掩膜和热图均已同步；共 **100 个物理层** 的掩膜与热图。
`validate_architecture.py --audit-out` 对 **386,938 行** CSV 检查域内包含、region 归属、
共同轴线、尺寸、有效采样、完整体积、绝对 Z 和物性范围，全部通过，记录见
[output-validation.json](../equivalence/output-validation.json)。
