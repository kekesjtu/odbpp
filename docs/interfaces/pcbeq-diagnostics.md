# pcbeq 运行诊断与产物清单

本接口定义等效器输出目录下的 `diagnostics/run.json`、`diagnostics/artifacts.json` 和 `diagnostics/image-artifacts.json`。诊断用于核对材料来源、几何验收、网格和采样配置；正式单元物性由 [MaterialMap](materialmap.md) 交付。解析器诊断和管道报告属于各自的输出目录，不使用本接口。

## 文件与发布边界

| 文件 | 内容与发布时机 |
| --- | --- |
| `diagnostics/run.json` | 一次成功数值计算的完整诊断，与 MaterialMap 一起发布 |
| `diagnostics/artifacts.json` | 数值发布管理的文件清单 |
| `diagnostics/image-artifacts.json` | 一次成功绘图发布管理的文件清单 |

`run.json` 表示成功交付的数值结果，不是逐事件日志。计算失败通过 stderr 和非零退出码报告，不发布本轮失败诊断；目录中已有的 `run.json` 可以属于上一次成功计算。绘图失败时，本轮已发布的数值结果仍可使用。事务回滚与文件所有权见[输出与发布](../decisions/output-publication.md)。

除字段名另有说明外，长度为 mm、面积为 mm²、耗时为秒。标识符区分大小写；材料配置中的层类型分类使用 `copper`、`dielectric`，层报告的 `type` 保留中间 JSON 的层类型。

## run.json 顶层

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `numerical_backend` | string | `rust` |
| `execution` | string | `native-json-to-materialmap` |
| `step` | string | 输入物理 step 名称 |
| `cell_count` | integer | 正式输出的全部物理层单元总数 |
| `raster` | object | 基础采样参考与各二维 region 的局部光栅 |
| `grid` | object | 二维 region 的局部网格与尺寸配置 |
| `rectification` | object | 有效 profile 的矩形近似及验收 |
| `layers` | array | 输入层目录对应的处理结果 |
| `materials` | object | 项目默认物性与各等效层的材料取值来源 |
| `diagnostics` | array | 本次成功计算产生的告警；无告警时为空数组 |
| `runtime` | object | `elapsed_s` 为数值结果写入关闭后的累计耗时，包含输入读取 |
| `performance` | object | 计时、规模和运行摘要 |

### 告警条目

```json
{
  "level": "warning",
  "code": "materials.default_used",
  "message": "materials.default_used: copper uses project defaults for k_w_mk",
  "path": null
}
```

`code` 用于分类，`message` 提供具体材料字段或层名。`path` 为位置字段，目前为 `null`，层定位信息包含在消息中。运行时告警也输出到 stderr；成功运行摘要的 `warnings` 保存相同消息字符串。

| code | 条件与含义 |
| --- | --- |
| `materials.default_used` | 至少一个材料字段采用项目默认值。消息列出 `copper` 或 `base` 及其字段；相同消息跨层去重，各层具体来源由 `materials.by_layer` 定位 |

Z 不可信本身不单独产生告警。钻孔覆盖仅根据 matrix 层序和起止铜层判断，层厚与绝对 Z 不参与该判断。非物理层跳过由 `layers` 记录。输入无效、必要跨度无法解析、几何约束不满足、采样不足或发布失败均作为错误终止。编译器、Clippy 和构建缓存告警不进入本文件。

## 层与材料

`layers` 按输入层顺序保存条目。所有条目都有 `name`、`type` 和 `status`。

| status | 其余字段 |
| --- | --- |
| `skipped` | `reason`：非 BOARD 上下文，或没有适用的铜/介质物理模型 |
| `equivalent` | 以下等效层字段 |

| 等效层字段 | 含义 |
| --- | --- |
| `domain_id`、`domain_source` | 层实体域引用及其来源；有效计算域由该域与整板域相交得到 |
| `cell_count` | 该物理层实际输出单元数 |
| `outside_material` | `base`：近似 region 超出原有效域的部分使用基底材料 |
| `outside_base_pixels` | 该层全部局部采样中落在有效域外的像素数；不是面积或体积折减系数 |
| `z_mm` | `{start_mm, end_mm, thickness_known, source}`；即使 Z 不可信，也保留 best-effort 数值和来源供核对 |
| `drill_overlays` | 参与该介质层材料采样的钻孔层名称数组；铜层为空数组 |

正式 MaterialMap 在 Z 不可信时使用 `[null, null]`；不能把诊断中的估算 Z 当作可信三维尺寸。

`materials.defaults` 包含 `copper`、`base` 的项目默认值及 `source`。`materials.by_layer` 以层名为键，每层包含 `phase1="copper"`、`phase2="base"` 和 `provenance`。两相均记录：

| 字段 | 含义 |
| --- | --- |
| `k_w_mk` | 热导率，W/(m·K) |
| `density_kg_m3` | 密度，kg/m³ |
| `specific_heat_j_kgk` | 比热容，J/(kg·K) |
| `fields` | 以上每个字段的 `{value, origin, source}` |

`origin` 为 `project_default`、`user_default`、`user_global`、`user_layer_type` 或 `layer_override`，按此顺序逐级覆盖。`source` 在项目默认时为参考来源说明，其余为对应的 `origin` 标签，不表示用户文件路径或文献。命令行逐字段覆盖并入 `global`。配置优先级与物理模型见[材料模型](../decisions/pcbeq/material-model.md)。

## 几何近似 rectification

| 字段 | 含义 |
| --- | --- |
| `ok`、`strategy` | 验收结果及 `fast-greedy-independent-profiles` 策略标识 |
| `rectangle_count` | 去重后二维 region 总数 |
| `profile_count` | 物理层引用的不同 domain ID 数 |
| `unique_profile_count`、`reused_profile_count` | 独立规划的有效几何数量及复用数量 |
| `relative_area_budget` | 各有效 profile 独立采用的相对面积预算 |
| `rectangles` | `{x0_mm, y0_mm, x1_mm, y1_mm}` 数组；数组下标是二维 `region_id` |
| `audit.domain_region_ids` | domain ID → `region_id` 数组 |
| `audit.domains` | domain ID → 单个有效 profile 的近似报告 |
| `audit.relative_area_budget` | 相对面积预算 |
| `audit.cross_layer_alignment_required` | `false`：不同有效 profile 无需跨层对齐 |
| `audit.per_profile_non_overlapping` | `true`：同一有效 profile 的矩形互不重叠 |
| `audit.effective_profile_identity` | 有效几何去重判据说明 |

`audit.domains[id]` 包含：

- 身份与关联：`canonical_domain_id`、`region_ids`；复用条目另有 `reused_from`。
- 结果与规模：`ok`、`strategy`、`rectangle_count`。
- 面积指标：`original_area_mm2`、`rectified_area_mm2`、`lost_area_mm2`、`gained_area_mm2`、`shape_deviation_mm2`、`relative_area_error`、`net_area_error_mm2`、`iou`。
- 验收：`audit.non_overlapping`、`audit.relative_area_budget`、`audit.area_budget_mm2`、`audit.topology`。
- 搜索诊断：需要 FAST 搜索时有 `audit.search`；矩形直接建模的条目不要求此字段。

设原始面积为 A、漏失为 L、新增为 G，则对称差为 L+G，相对误差为 `(L+G)/A`，净面积误差为 G−L，IoU 为 `(A−L)/(A+G)`。模型面积和物性对应完整近似 region。

`audit.topology` 的字段为：

| 字段 | 含义 |
| --- | --- |
| `original_components`、`model_components` | 原域、模型的实体数量 |
| `original_holes`、`model_holes` | 原域、模型的孔洞数量 |
| `component_correspondence`、`reverse_component_correspondence` | 原实体与模型实体的双向对应数量数组 |
| `hole_correspondence`、`reverse_hole_correspondence` | 孔洞的双向对应数量数组 |
| `correspondence` | `positive-area overlap`，以正面积相交判定对应 |
| `positive_width_connections` | 正宽连接检查是否通过 |
| `protected_core_checks` | `{erosion_mm, cores, retained}` 数组：给定腐蚀尺度下的几何核心数与保留数 |
| `preserved` | 拓扑和保护核心综合验收结果 |

`audit.search` 保存 `pixels`（搜索列数、行数）、`resolution`、`tau`、`attempts`、`max_rectangles`、`max_search_cells`，以及 `seed`、`benefit`、`edge_search` 算法说明。每次尝试记录 `resolution`、`tau`、`ok`；失败尝试还可带 `rectangle_count`、`estimated_error_mm2` 和当时的 `audit`。这些参数属于几何搜索，与材料采样光栅分开解释。约束语义见[几何规划](../decisions/pcbeq/geometry-planning.md)。

## 网格与采样

诊断中的 `region_id` 引用 `rectification.rectangles`，代表可跨物理层复用的二维区域。MaterialMap 的 region 则是二维 region 与某个物理层的组合；不能直接用 MaterialMap 数组下标查诊断 region。

### grid

| 字段 | 含义 |
| --- | --- |
| `layout`、`conformity_required` | `region-local-cartesian`、`false` |
| `region_count` | 去重后二维 region 数量 |
| `cell_count` | 每个二维 region 计算一次的网格单元总数，不含物理层重复 |
| `regions` | `{region_id, bounds_mm: {x: [low, high], y: [low, high]}, grid_counts: {x, y, z}}` 数组；`z=1` |
| `size_constraints_mm.max` | X/Y 最大单元边长数组 |
| `mesh_configuration` | 网格配置与特征尺寸估计 |

`mesh_configuration` 均有 `mode`、`minimum_samples_per_axis`、`max_cell_mm`、`raster`（与顶层局部采样报告同源）。`mode="expert"` 使用显式尺寸；`mode="preset"` 另有 `preset`、`feature_estimator`、`feature_estimate_min_mm`、`feature_estimate_representative_mm`、`feature_estimate_count`、`ignored_sliver_estimate_count`、`board_scale_divisions`、`feature_multiplier` 和 `target_max_cell_mm`。没有可用估计时，相应特征尺寸为 `null`。

### raster

| 字段 | 含义 |
| --- | --- |
| `board_bounds_mm` | 整板包围盒 `[x0, y0, x1, y1]` |
| `layout`、`mode` | `region-local`；`automatic` 或 `explicit` |
| `px`、`py` | 整板基础参考像素数，不是最大局部光栅尺寸 |
| `pixel_step_mm`、`base_pixel_step_mm` | X/Y 基础参考像素间距 |
| `default_long_side_px`、`max_region_pixels` | 默认基础长边像素数与单个局部光栅容量上限 |
| `total_pixels`、`peak_region_pixels` | 各二维 region 局部像素总数与最大值，不含物理层重复采样 |
| `regions` | `{region_id, px, py, origin_mm: [x, y], pixel_step_mm: [dx, dy]}` 数组 |

局部光栅像素中心为 `origin+(i+0.5)*pixel_step`。自动模式允许局部加密，因此应使用各 region 的实际间距恢复采样位置。覆盖规则见[材料采样](../decisions/pcbeq/material-sampling.md)。

## 性能摘要与 stdout

`performance` 包含 `durations_s`、`cells`、`regions`、`layers`、`raster`、`cells_per_s`、`materialmap_bytes`、`warnings`、`numerical_backend`、`execution`、`geometry_backend` 和 `raster_sampling`。

- `cells`、`regions`、`layers` 为正式输出的单元数、三维 region 数和等效物理层数。
- `raster` 为 `[px, py]`，`raster_sampling` 为局部采样报告。
- `materialmap_bytes` 为正式 JSON 文件字节数；`cells_per_s` 为输出单元数除以总耗时。
- `warnings` 为消息字符串数组，结构化条目见顶层 `diagnostics`。
- `durations_s` 包含 `read_json`、`equivalence_and_write`、`total`、`geometry`、`mesh`、`sampling`、`write`。其中读入包含输入校验；细分阶段存在未单列的调度开销，不要求加总等于总耗时。

诊断文件的计时截止于数值结果写入关闭，不包含最终事务发布。`--summary-json` 输出到 stdout 的摘要在发布完成后补充 `durations_s.publication`，并把发布耗时计入 `total`、`equivalence_and_write`、`write`，重新计算 `cells_per_s`。读取两处计时时应保留这一口径差异。

## 产物清单

两个清单都使用以下结构，路径相对于本次输出目录，使用 `/` 分隔：

```json
{
  "files": ["materialmap.json", "diagnostics/run.json"]
}
```

数值清单登记 MaterialMap 和运行诊断。图片清单登记实际请求并成功生成的 PNG、SVG、`index.html` 等文件；清单本身由发布器管理，不包含在其自身 `files` 中。

清单用于界定程序管理的文件，重跑时清理相应旧产物并保留未登记的用户文件。数值重算发布会清理已登记的旧图片和图片清单，图片由后续绘图独立发布。清单不保存文件内容、物理验收结果或内容指纹。
