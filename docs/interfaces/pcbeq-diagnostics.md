# pcbeq 运行诊断

等效器输出目录只使用一个 `diagnostics.json` 保存运行诊断，不输出产物清单。报告按几何统计、告警与错误、误差分析、性能分析四类组织。正式区域、局部网格和五项物性由 [MaterialMap](materialmap.md) 提供。

长度单位为 mm，面积为 mm²，时间为秒。层名和 profile ID 区分大小写。

## 顶层结构

| 字段 | 内容 |
| --- | --- |
| `geometry` | 不同有效 profile 数量及各 profile 的层、region、网格统计 |
| `issues` | 运行状态、材料默认值告警、Z 不可信及阻断错误 |
| `accuracy` | 各 profile 的面积误差与三项拓扑检查 |
| `performance` | 不重叠的阶段耗时与总耗时 |

`geometry.profiles` 与 `accuracy.profiles` 使用相同的 profile ID。profile 表示参与等效的二维有效几何，域的物理语义见[几何规划](../decisions/pcbeq/geometry-planning.md)。几何相同的输入 profile 共用一项统计，使用其中一个输入 ID。只有参与等效的 BOARD 铜层与介质层计入统计。

## 几何统计 geometry

`profile_count` 表示独立有效几何数量；`profiles` 以 profile ID 为键，各项包含：

| 字段 | 口径 |
| --- | --- |
| `layers`、`layer_count` | 复用该有效几何的物理层名列表与数量 |
| `region_count` | 独立二维矩形数量，不乘复用层数 |
| `cell_count` | 独立二维网格单元总数，不乘复用层数 |
| `region_size_mm` | region 的实际 X/Y 边长范围 |
| `cell_size_mm` | 局部均匀网格的实际 X/Y 单元边长范围 |

两个尺寸字段均为 `{"min":{"x":值,"y":值},"max":{"x":值,"y":值}}`。X/Y 独立取极值；最小 X 与最小 Y 不要求来自同一个矩形。尺寸不包含 Z，不代表面积、对角线或用户指定的网格上限。

一个 profile 被 8 层复用、划分成 20 个 region 和 1000 个网格时，报告记录 `layer_count=8`、`region_count=20`、`cell_count=1000`。MaterialMap 相应交付 160 个三维 region 和 8000 个物性单元。

## 告警与错误 issues

`status` 为 `completed`、`completed_with_warnings`、`completed_with_errors` 或 `failed`。`warnings`、`errors` 均为数组，无问题时为空。非阻断错误优先于告警决定状态。

- `materials.default_used`：任一材料字段采用内置项目默认值时告警。条目记录 `code`、`message`、`layers`、`phase` 和 `default_values`。`phase` 为 `copper` 或 `base`，`default_values` 给出实际采用内置值的字段与数值；同相且默认字段和值相同的条目跨层合并。用户显式提供与内置值相同的数值不触发告警。
- `z.thickness_unknown`：本层 `z_mm.thickness_mm=null`，记录非阻断错误。
- `z.absolute_position_unknown`：`z_mm.start_mm=null` 且本层厚度已知，记录非阻断错误。

Z 错误记录 `code`、`message`、`layers`、`blocking=false`。同一层两者均为 `null` 时记 `z.thickness_unknown`。
MaterialMap 对任一未知侧使用 `[null,null]`，仍交付完整 XY 网格与物性，不据此计算可信体积或质量。

输入无效、缺少必要几何、无法满足近似约束、局部采样光栅超限或发布失败属于阻断错误。条目记录阶段对应的稳定代码 `<stage>.failed`、`stage`、`message` 和 `blocking=true`；消息保留可用的层、profile、region 或单元位置。

正常完成包括存在非阻断错误的情况，CLI 返回 0，问题同时写到 stderr，后续绘图可继续。阻断失败返回 2。计算过程中出错时，若输出位置允许写入且安全，尽力发布本轮 `failed` 诊断。未完成或未知的统计使用 `null`，已完成的统计保留。目录中可能仍有上一轮成功发布的 MaterialMap。CLI 参数解析、内联材料 JSON 解析和输出覆盖输入的检查均可能在计算诊断开始前失败，此时由 CLI 报告。

## 误差分析 accuracy

每个 `profiles[id]` 含 `area` 和 `topology`。

`area` 保存 `original_mm2`、`model_mm2`、`lost_mm2`、`gained_mm2`、`relative_error`、`allowed_relative_error` 与 `passed`。原始面积 A 为有效 profile 的面积，漏失为 L，新增为 G：

- 模型面积：`A-L+G`。
- 主验收误差：`(L+G)/A`，不是可相互抵消的净面积差。
- 面积通过条件：`L+G <= allowed_relative_error*A + 1e-8 mm²`。

`topology` 只交付三项检查及综合结论：

| 字段 | 判定 |
| --- | --- |
| `components` | 原始实体与模型实体按正面积重叠双向一一对应 |
| `holes` | 原始孔洞与模型孔洞按正面积重叠双向一一对应 |
| `positive_width_connections` | 模型实体向内收缩极小距离后仍为单个实体，防止点接触冒充有效连接 |
| `preserved` | 上述三项均通过 |

`components`、`holes` 均包含 `original_count`、`model_count`、`forward_matches`、`reverse_matches`、`preserved`。对应数量数组分别按原始几何和模型几何排列；相交面积须大于 `1e-10 mm²`。正宽检查的收缩距离为 `1e-7 mm`，不保证连接宽度准确。

保护核心继续作为内部局部覆盖约束参与候选验收，其尺度、数量及结果不写入诊断。面积和拓扑分别验收，几何近似误差不代表热仿真误差。

## 性能分析 performance

`stages_s` 保存互不重叠的累计墙钟耗时：

| 阶段 | 范围 |
| --- | --- |
| `read_validate` | 参数检查、JSON 读取与契约校验 |
| `prepare` | 材料配置、坐标规整、层前置检查、钻孔关系等准备 |
| `profiles` | 完整层实体范围准备、有效几何去重 |
| `rectification` | region 近似搜索、面积与内部几何约束验收 |
| `mesh` | 局部网格划分与采样窗口准备 |
| `sampling` | 材料几何索引准备与局部光栅生成 |
| `equivalence` | 样本积分、单元物性计算与数值检查 |
| `write` | 结果暂存及 MaterialMap 序列化、写出与关闭 |
| `publication` | 事务发布至 MaterialMap 安装完成 |
| `other` | 调度、报告统计等未归入上述阶段的开销 |

`total_s` 从计算入口计至 MaterialMap 安装完成，包括 `other`；分项之和等于总耗时，允许浮点舍入差。循环处理多个 profile、region、层时累计计时。失败时总耗时截至失败，未执行阶段为 `null`，已进入阶段记录截至错误的耗时。诊断自身最后一次序列化、写入和安装、事务清理、独立 Python 绘图不计入总耗时。

`--summary-json` 的 stdout 摘要供进程调度和基准使用，包含与诊断相同的 `performance`、`issues`，另有物理总量 `cells`、`regions`、`layers`、`cells_per_s`、`materialmap_bytes`，不写成额外文件。
