# pcbeq 运行诊断与产物清单

本接口定义等效器输出目录下的 `diagnostics/run.json`、`diagnostics/artifacts.json` 和 `diagnostics/image-artifacts.json`。正式区域、网格和物性由 [MaterialMap](materialmap.md) 提供；诊断保存计算依据、材料来源、几何误差、采样配置及运行信息。解析器诊断和管道报告属于各自的输出目录。

## 发布边界

`run.json` 与 MaterialMap 一起发布，表示一次成功数值计算，不是逐事件日志。计算失败通过 stderr 和非零退出码报告，不发布本轮失败诊断；目录中已有的诊断可以属于上一次成功计算。绘图失败时，已发布的数值结果仍可使用。事务与所有权规则见[输出与发布](../decisions/output-publication.md)。

长度为 mm，面积为 mm²，耗时为秒。层名和 domain ID 区分大小写。

## run.json 顶层与关联

| 字段 | 内容 |
| --- | --- |
| `rectification` | 有效 profile 去重关系、几何误差与拓扑验收 |
| `grid` | 实际网格尺寸约束、档位和特征估计 |
| `raster` | 基础采样参考与各独立局部光栅 |
| `layers` | 层对应的有效域、材料采样诊断、未知 Z 或跳过原因 |
| `materials` | 以等效层名为键的逐字段材料取值与配置来源 |
| `warnings` | `{code, message}` 数组；无告警时为空数组 |
| `durations_s` | 数值阶段耗时 |

诊断中的 `region` 和 profile 的 `regions[]` 都直接引用同轮 `materialmap.json` 的 `regions` 数组下标，从 0 开始。多个物理层复用同一二维 profile 时，以首次发布的对应 region 为代表。通过该代表的 XY 范围和网格数恢复几何与采样关联；该代表的层名和 Z 不限制其他复用层。

step、层类型、可信 Z、矩形坐标、网格数和单元数从 MaterialMap 读取。二维唯一 region 数等于 `raster.regions` 长度，物理 region 数等于 MaterialMap 的 region 数，物理单元数为各 region 的 `values` 长度之和。

## 几何近似 rectification

| 字段 | 内容 |
| --- | --- |
| `max_area_error_ratio` | 各有效 profile 独立采用的相对面积预算 |
| `domain_profiles` | 输入 domain ID → 规范 profile ID，表达有效几何去重与复用关系 |
| `profiles` | 以规范 profile ID 为键的独立几何报告，每个有效几何只保存一份 |

每个 `profiles[id]` 包含：

| 字段 | 内容 |
| --- | --- |
| `regions` | 该二维 profile 的 MaterialMap 代表 region 下标数组 |
| `original_area_mm2` | 与整板实体范围相交后的原始有效面积 |
| `lost_area_mm2`、`gained_area_mm2` | 矩形模型相对原域的漏失、新增面积 |
| `topology` | 实体、孔洞、正宽连接与保护核心验收 |
| `search` | 需要 FAST 搜索时的实际搜索信息；直接矩形建模时省略 |

设原始面积为 A、漏失为 L、新增为 G，则模型面积为 `A-L+G`，也可由代表 region 的矩形面积相加得到；对称差为 `L+G`，相对误差为 `(L+G)/A`，IoU 为 `(A-L)/(A+G)`。面积和拓扑约束见[几何规划](../decisions/pcbeq/geometry-planning.md)。

`topology` 包含：

- `component_correspondence`、`reverse_component_correspondence`：原实体与模型实体的双向对应数量数组。
- `hole_correspondence`、`reverse_hole_correspondence`：原孔洞与模型孔洞的双向对应数量数组。对应以正面积相交判定；数组长度分别给出原域和模型的实体或孔洞数量。
- `positive_width_connections`：正宽连接检查结果。
- `protected_core_checks`：`{erosion_mm, cores, retained}` 数组，记录腐蚀尺度、几何核心数及保留数。
- `preserved`：拓扑和保护核心综合验收结果。

`search` 包含 `pixels`（搜索列数、行数）、`resolution`、`tau` 和 `attempts`。尝试条目记录 `resolution`、`tau`、`ok`；失败尝试还可有 `rectangle_count`、`estimated_error_mm2` 和当时的 `audit`。搜索信息用于解释几何规划，不是材料采样光栅。

## 网格与采样

`grid` 均有 `max_cell_mm`（X/Y 最大边长数组）和 `minimum_samples_per_axis`。档位模式另有 `preset`、`feature_estimate_min_mm`、`feature_estimate_representative_mm`、`feature_estimate_count`、`ignored_sliver_estimate_count`。没有可用特征估计时，相应尺寸为 `null`；没有 `preset` 时采用专家尺寸。具体分格数从代表 region 的 `grid_counts` 读取。

`raster` 包含：

| 字段 | 内容 |
| --- | --- |
| `mode` | `automatic` 或 `explicit` |
| `board_bounds_mm` | 原始整板包围盒 `[x0,y0,x1,y1]`，用于定义基础参考相位 |
| `px`、`py` | 整板基础参考像素数 |
| `regions` | `{region, px, py, origin_mm: [x,y], pixel_step_mm: [dx,dy]}` 数组，每个独立二维 region 一项 |

基础像素间距由整板包围盒尺寸除以基础像素数得到。实际局部像素中心为 `origin+(i+0.5)*pixel_step`。自动模式允许局部加密，应使用局部实际间距恢复采样位置。总像素数与峰值分别为各条目 `px*py` 的和与最大值，不含物理层重复采样。覆盖规则见[材料采样](../decisions/pcbeq/material-sampling.md)。

## 层与材料

`layers` 按输入层顺序保存条目，每项都有 `name`。跳过层记录 `reason`；等效层记录：

| 字段 | 内容 |
| --- | --- |
| `domain_id` | 输入层实体域引用，经 `rectification.domain_profiles` 找到有效 profile |
| `outside_base_pixels` | 有效域外、按基底处理的局部采样像素总数，不是体积折减系数 |
| `drill_overlays` | 仅介质层记录，值为参与采样的钻孔层名数组 |
| `estimated_z_mm` | 仅绝对 Z 不可信时记录 `{start_mm,end_mm,source}`，供工程核对 |

可信 Z 从 MaterialMap 读取。不可信 Z 在 MaterialMap 中为 `[null,null]`；不能把诊断中的估算值当作可信三维尺寸。

`materials[layer_name]` 有 `copper` 和 `base` 两相，每相的 `k_w_mk`、`density_kg_m3`、`specific_heat_j_kgk` 均为 `{value, origin}`。单位分别为 W/(m·K)、kg/m³、J/(kg·K)。

`origin` 为 `project_default`、`user_default`、`user_global`、`user_layer_type` 或 `layer_override`，依次覆盖。命令行逐字段覆盖并入 `global`。项目默认值、参考来源和配置优先级见[材料模型](../decisions/pcbeq/material-model.md)。

## 告警与耗时

`warnings` 保存告警代码与完整消息；运行时也输出到 stderr。`materials.default_used` 表示至少一个材料字段采用项目默认值，消息列出材料相与字段，相同消息跨层去重；具体层来源见 `materials`。

钻孔覆盖只比较介质 row 与起止铜层 row。Z 不可信本身不单独告警；输入无效、必要端点无法解析、几何约束不满足、采样不足或发布失败均作为错误终止。构建工具告警不进入本文件。

`durations_s` 包含 `read_json`、`equivalence_and_write`、`total`、`geometry`、`mesh`、`sampling`、`write`。读入包含输入校验；细分阶段存在未单列的调度开销，不要求加总等于总耗时。文件计时截止于数值结果写入关闭，不含最终事务发布。

`--summary-json` 的 stdout 摘要用于进程调度和基准，包含上述耗时及 `cells`、`regions`、`layers`、`cells_per_s`、`materialmap_bytes`、`warnings`（消息字符串数组）。发布后补充 `durations_s.publication`，将发布耗时加入 `total`、`equivalence_and_write`、`write` 并重算吞吐率；该摘要与文件诊断是不同用途的输出。

## 产物清单

两个清单都使用 `{"files":["相对路径", ...]}`，路径相对本次输出目录，使用 `/` 分隔。数值清单登记 `materialmap.json`、`diagnostics/run.json`；图片清单登记实际请求并成功生成的 PNG、SVG、`index.html` 等文件。清单本身由发布器管理，不包含在自身 `files` 中。

数值重算发布清理已登记的旧图片和图片清单，图片由后续绘图独立发布。未登记的用户文件保留。
