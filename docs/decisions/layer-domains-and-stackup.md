# 逐层计算域与绝对叠层

## 计算域决策

每个物理层的计算域优先使用有效层级 profile；缺失时使用 step profile；存在但无法解析时报告该层失败。
step profile 定义整板范围。等效器使用 layer profile 与 step profile 的交集作为该层有效实体域。
审计分别记录 authored area、越出 step 的面积及有效域内的模型损失。

刚挠板各层的实体域可以不同。几何规划阶段将域差异切分为共享 region，每层明确登记整块存在性。
铜图案描述材料分布，逐层域描述物理层在哪里存在；域内白色基底也属于完整实体。
介质 JSON 保留未扣 DRILL 的基底几何；预览在绘图副本中扣除相交钻孔。

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

DRILL 保存投影和 `copper_span`，绝对跨度由起点铜层的 Z 起始和终点铜层的 Z 终止推导。
它是跨层图案，材料采样仅覆盖相交介质层。

## 依据与实现对照

真实刚挠案例的局部层级 profile、INCH 铜重和盲埋孔提供输入依据。
`pcbkit.model.Step.base_geometry` 选择基底，`pcbkit.stackup` 解释层次和厚度，`pcbkit.export` 输出域引用与层目录。
等效器规划和采样分别消费域与跨度，接口形状见 [中间 JSON](../interfaces/intermediate-json.md)。
