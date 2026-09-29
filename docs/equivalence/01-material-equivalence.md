# 01 · PCB 物性等效器（数据流 4-5）

AGENTS「总体数据流结构」的第 4、5 段。输入是解析器写出的中间 JSON
（`odbpp/preview-2`），输出是一张逐物理层、逐网格单元的等效物性表
`*.MaterialMap.csv` 与同名旁注 `*.MaterialMap.meta.json`。

```text
[ 中间格式 .json ]  schema = odbpp/preview-2
        │
        ▼
[ 4. PCB 物性等效器 ]  equivalence_from_document()
        │  · 矩形化 step profile    (rectify.py)
        │  · 整板共享 XY 网格       (grid.py)
        │  · 逐层黑白光栅           (raster.py)
        │  · 两材料 k / ρ / cp      (materials.py)
        ▼
[ 5. MaterialMap.csv + meta.json ]  ← 数值唯一依据
        │                              (equiv.write_materialmap)
        └─ 另出只给人看的图：masks/<layer>.png、heatmaps/<layer>.png (visualize.py)
```

第一版只对**铜层**与**介质层**做等效。钻孔是跨层图案，不作为独立厚度层；
DOCUMENT、COMPONENT、SOLDERMASK、ROUT 等一律跳过，并逐层给出稳定 code 的跳过原因。
更完整的边界与公式约定见 `AGENTS.md`「等效器输入与材料规则」「外轮廓矩形化与网格」
「等效公式与输出」三节；本文只记录**实现**如何落实它们，以及实测结果与已知近似。

---

## 1. 输入契约

等效器**只**接受 `schema == "odbpp/preview-2"`。preview-1 没有逐层计算域，
会直接抛出 `EquivalenceError`（CLI 退出码 2，不写结果），不会静默退化为
「用铜图案当计算域」。

每层必须能解析出：

| 字段 | 用途 |
|---|---|
| `layers[].polygons[]` | 该层的正向实体图案（铜/介质），用于黑/白掩膜 |
| `layers[].domain_id` → `domains[]` | 该层的二维计算域；决定该层在某网格单元内**是否存在** |
| `layers[].z_mm` | 绝对 Z 区间（mm，自叠层顶面向下）与 `thickness_known` |
| `domains[]` 中 `step_profile` | 整板网格范围与矩形化的输入轮廓 |

`domain_source` 区分 `step_profile` 与 `layer_profile`。层级 profile 缺失回退到
step profile，缺失**且**无法解析时报错——回退与否由解析器决定并写进 JSON，
等效器不再重新解释 ODB++ 图元极性。

## 2. 矩形化（`rectify.py`）

step profile 被分解成一串**轴对齐、内部互不重叠**的矩形 region，形成阶梯状边界。

- **垂直条带分解**：按所有顶点 x 坐标切条带；条带内上下边沿不再变化，直接配对成矩形。
- **斜边离散**：横截面在条带内变化的条带（斜边、圆弧）按 `tolerance_mm` 细分成阶梯。
  顶点 x 坐标先吸附到 tolerance 网格（整数 key），因此每个子条带宽度恰为
  `tolerance` 的整数倍，最窄不小于一个 tolerance。这一步避免了「按固定步数均分导致
  某些子条带窄于 tolerance、网格被迫过密」的问题。本步最后一格的右边界直接取吸附
  边界本身，不用 `起点 + 步长` 累加，避免浮点尾巴留下零宽子条带。
- **交点先配对、后吸附**：一条扫描线的交点序列必须先按奇偶配对成「实体/空隙」，
  再把每个端点吸附到容差网格，最后才合并被吸附到一起的相邻区间。顺序反过来时，
  比 tolerance 还窄的空隙会把两个交点并成一个键，键数变奇数，**整条竖直条带被丢弃**；
  相邻条带还会被跨缺口合并，把没测量到的轮廓当成实体。
- **孔洞**：even-odd 区间配对，孔洞天然不产生矩形；孔洞被阶梯完全填平时单独报
  `rectify.hole_filled`。
- **精度误差**：分别记录 `lost_area`（岛丢失）与 `gained_area`（孔被填），
  以及 `net_area_error`、`shape_deviation = |lost| + |gained|`、`relative_area_error`。
  每个岛/孔单独检查，避免「丢一个岛 + 填一个孔」在净面积上相互抵消。

**精度默认值**：`tolerance_mm = None` 表示**由光栅像素间距决定**，取
`2 × max(pixel_step_x, pixel_step_y)`。原因：region 边界是强制网格线，若
tolerance 小于像素间距，就会产生「单元内没有任何像素中心」的网格，
与约束 5 冲突。取两倍间距留出分配余量。

**误差判据**：`allowed = max(max_area_error_mm2, max_area_error_ratio × area,
perimeter × tolerance)`。前两项是用户可配的绝对/相对下限，第三项是**阶梯本身固有**的
上界——边界每点最多移动约一个 tolerance，故对称差不超过 `周长 × tolerance`。
超过它才说明**确实丢了特征**（而不是「阶梯近似」），此时报 `rectify.area_error`。
`max_absolute_area_error_mm2` 默认 0.5 mm²，`max_relative_area_error` 默认 0.01。

周长上界在大板 + 粗容差下可能远宽于配置的相对上限，于是「移动了 2% 板面」也能通过。
结果仍在周长上界内、但已超过配置上限时记 `rectify.area_warning`（warning 级，仍接受、
仍写结果），使粗光栅下的轮廓损失至少可被看见——真实 case 的实测值见 §9.2/§9.3。
若某条带仍无法判读（奇数交点，只可能来自自交/退化输入），记
`rectify.slab_ambiguous`（error 级）并置 `ok=false`，绝不静默跳过。

## 3. 网格（`grid.py`）

- **强制网格线**：所有 region 的 x/y 边界必然是网格线。
- **分配余量**：`nx`、`ny` 是**整板**各方向总格数（不是每 region 格数），
  未给定时等于 `px`、`py`，且必须满足 `1 ≤ nx ≤ px`、`1 ≤ ny ≤ py`。
- **最大余数法**：剩余格数按各区间「还能容纳的像素数」加权分配——优先把格线放在
  像素储备充足的区间，避免产生无像素单元。
- **不越权**：强制边界数超过 `nx`/`ny` 能容纳的数量时报
  `grid.forced_lines_overflow`，**绝不**擅自增加用户指定的格数。
- 每个单元完整落在单个 region 内，不跨越 region 边界。板外不产生网格。

## 4. 光栅化（`raster.py`）

- **像素中心法**：单元是否含某像素，以其**中心**是否落在单元内判定；
  `nx/ny` 不必与 `px/py` 对齐。
- **even-odd 扫描线**：逐像素行求交点、奇偶配对，正确处理岛、孔与嵌套，
  与环的绘制顺序无关。
- 某层在某个有效网格单元内**没有任何像素**时报 `equiv.cell_without_pixel`
  （致命：CLI 视为不可信，不写结果），不会静默取 0 或最近像素。
- 逐层/分块处理，绝不同时持有整板光栅。

## 5. 材料（`materials.py`）

两相模型：**材料 1 = 铜（黑）**，**材料 2 = 基底（白）**。每相需要 `k`、`ρ`、`cp`。

优先级（高→低）：**逐层覆盖 > 用户全局覆盖 > 项目默认值**。
一旦用到项目默认值就记 `materials.default_used` 警告（仍正常出结果）。
字段非法记 `materials.invalid`，该相退回默认值并继续。

项目默认值（数值与来源都写进 meta）：

| 相 | k (W/m·K) | ρ (kg/m³) | cp (J/kg·K) | 来源 |
|---|---|---|---|---|
| 铜（黑） | 385 | 8960 | 385 | 纯退火铜 ~25 °C，CRC Handbook；ρ 与 `units.DEFAULT_COPPER_DENSITY_G_CM3` 一致 |
| 基底（白） | 0.30 | 1850 | 1100 | 固化 FR-4 典型 datasheet 值，如 Isola 370HR |

FR-4 是一个材料族而非化合物，默认值只代表典型值；真实叠层请用逐层覆盖。

## 6. 等效公式

设某单元在两材料图案中，黑色（铜）面积分数为 φ，则按
`docs/derivation/串并联PCB等效热模型推导.md` 与
`docs/derivation/密度和热容等效热模型推导.md` 的像素离散形式：

```text
kx = mean_rows( 1 / mean_cols_in_row( 1 / k ) )      # 行内串联、行间并联
ky = mean_cols( 1 / mean_rows_in_col( 1 / k ) )      # 列内串联、列间并联
kz = φ·k1 + (1-φ)·k2                                 # 柱间并联
ρ  = φ·ρ1 + (1-φ)·ρ2                                 # 体积分数加权
cp = ( φ·ρ1·cp1 + (1-φ)·ρ2·cp2 ) / ρ                 # 质量加权
```

即每一像素行内对 `1/k` 取列平均后再取倒数（行内调和/串联），对行取算术平均
（行间并联）；`ky` 对称。切勿写成对行平均再取倒数的形式。

黑/白由该层的正向实体图案与该层计算域共同决定：

- **域外**：该层在此单元不存在，不生成单元，绝不当作 FR4。
- **域内**：图案覆盖处为铜（黑），其余为基底（白）。

### 6.1 钻孔规则

与某物理层 Z 区间相交的钻孔，其**投影**在该层内视为**全铜、全黑**（`drill_model =
solid_copper`）。规则对铜层与介质层同样适用：先在黑/白掩膜上叠加钻孔，
再统计 φ。绝不会把钻孔当空气，也不会重复扣除已从介质多边形中扣掉的孔。

匹配钻孔时**优先使用 JSON 的铜层序号／`z_span` 拓扑跨度**（铜层按平面序号
包含端点，介质层按半开区间）：层厚缺失时多个层的最优估计 Z 可能同时为 0，
只用绝对 Z 会把钻孔错误投到它并未到达的层。仅当文档没有提供跨度时才退回
绝对 Z，并在层厚未知时记 `equiv.drill_match_z_fallback`；拓扑与 Z 结论不一致
时记 `equiv.drill_z_mismatch` 并以拓扑为准。钻孔跨度完全无法确定时报
`equiv.drill_span_unknown`（致命），不擅自视为贯穿全板。

## 7. 输出

### 7.1 `*.MaterialMap.csv`

自描述表头，每个量都带单位。列（`CSV_COLUMNS`）：

```text
layer, layer_type, region_id, cell_i, cell_j,
cell_x0_mm, cell_y0_mm, cell_x1_mm, cell_y1_mm,
z_start_mm, z_end_mm, z_thickness_known,
copper_area_fraction, kx_w_mk, ky_w_mk, kz_w_mk,
density_kg_m3, specific_heat_j_kgk,
pixel_rows, pixel_cols, pixel_count,
cell_area_mm2, domain_area_mm2, domain_coverage, partial_domain
```

`cell_x0_mm`…`cell_y1_mm` 是**全板共享网格单元**的边界，不随逐层计算域收缩。
第 6 节求得的 `kx`/`ky`/`kz`/`density`/`specific_heat` 是该单元内**被该层计算域
覆盖部分**的物性。`domain_coverage = domain_area_mm2 / cell_area_mm2` 明确给出
覆盖比例；板外剩余部分不是 FR4。下游计算单元质量与热容时必须乘以
`domain_coverage`，不得把未覆盖部分当作基底材料补足。

该布局是本项目自己的设计：参考 `docs/Flotherm_reference_MaterialMap.csv`
看了「需要表达什么」，没有照抄，也不以 Flotherm 可导入为目标。

### 7.2 `*.MaterialMap.meta.json`（`odbpp/materialmap-2`）

CSV 无法表达或不宜逐行重复的信息：

- `units`：长度/面积/k/ρ/cp 的统一单位（一律 mm、mm²、W/(m·K)、kg/m³、J/(kg·K)）；
- `z_axis`：绝对 Z 方向约定（自顶面向下）；
- `drill_model`：固定 `solid_copper`；
- `coverage`：`cell_area_mm2`/`domain_area_mm2`/`domain_coverage` 三列的下游语义
  （覆盖部分才是该层材料，剩余为板外/空洞，按覆盖率折算质量与热容）；
- `raster`：`px`/`py`/`pixel_step_mm`/`board_bounds_mm`/`sampling`；
- `grid`：`nx`/`ny`/强制线与总数/`region_count`/`cell_count`/网格线坐标；
- `rectification`：`tolerance_mm`/原始与矩形化面积/`lost`/`gained`/`net`/
  `shape_deviation`/`relative_area_error`/`ok`/矩形清单；
- `materials`：`defaults`（数值+来源）与 `by_layer`（每层实际取值及来源）；
  每层 provenance 的 `fields.<name>` 逐字段记录 `value`/`origin`/`source`，
  因此只覆盖铜的热导率时，其密度与比热仍显式标记为 `project_default`；
- `layers[]`：每层 `status`（`equivalent`/`skipped`/`failed`）、`reason`、
  `domain_id`、`domain_source`、`z_mm`、`drill_overlays`、`cell_count`；
- `diagnostics[]`：稳定 code 的诊断列表（level/code/message/path）。

`materialmap-1` 的布局不接受此版本的覆盖率列；读取方必须按 schema 判断。

### 7.3 图像产物（不参与数值）

默认还会在 `<out>/` 下写：`masks/<layer>.png`（逐层黑白光栅）、
`heatmaps/<layer>.png`（逐层 `kx|ky|kz` 三面板热图）、
`<step>.images.html`（图例页：层名/类型/绝对 Z/k 范围/单元数）。
它们只是同一张 CSV 的另一种看法，可以用 `--no-raster-png`/`--no-heatmap`/
`--no-gallery` 关掉；语义、色标与失败处理见 [`02-visualization.md`](02-visualization.md)。

## 8. 诊断 code

| code | 含义 |
|---|---|
| `rectify.empty_profile` | 轮廓为空，无法矩形化 |
| `rectify.no_rectangles` | 矩形化后无 region |
| `rectify.area_error` | 形状偏差超过精度允许的上界 |
| `rectify.island_lost` | 某岛窄于 tolerance，被丢弃 |
| `rectify.hole_filled` | 某孔窄于 tolerance，被填平 |
| `rectify.area_warning` | 轮廓移动超过配置上限、但仍在周长上界内（仍接受） |
| `rectify.slab_ambiguous` | 某条带交点为奇数，无法判读，已跳过（置 `ok=false`） |
| `grid.forced_lines_overflow` | 强制边界数超过用户指定格数 |
| `grid.no_board_cells` | 网格内没有板内单元 |
| `grid.cell_without_pixel` | 网格单元内无像素中心（致命） |
| `equiv.unsupported_schema` | 非 preview-2（抛 `EquivalenceError`，非诊断） |
| `equiv.layer_failed` | 某层无法等效 |
| `equiv.layer_skipped` | 某层按规则跳过（附原因） |
| `equiv.missing_domain` | 引用的计算域不存在 |
| `equiv.drill_span_unknown` | 钻孔 Z 跨度无法确定（致命） |
| `equiv.drill_match_z_fallback` | 无拓扑跨度、只能按绝对 Z 匹配且层厚未知 |
| `equiv.drill_z_mismatch` | 拓扑跨度与绝对 Z 结论不一致（以拓扑为准） |
| `equiv.layer_z_unknown` | 层 Z 区间缺失，只能 best-effort |
| `equiv.partial_domain` | 计算域只覆盖部分网格单元（默认按层汇总告警） |
| `equiv.layer_empty` | 该层在其计算域内没有任何实体 |
| `equiv.dielectric_features_ignored` | 介质层上非钻孔特征被忽略 |
| `materials.default_used` | 某相/某物性字段使用了项目默认材料 |
| `materials.invalid` | 材料字段非法（非有限正数/非数字），已退回默认值 |
| `visualize.failed` | 掩膜/热图渲染失败（警告，CSV 与 meta 仍已写出） |

退出码：`0` 无 error 级诊断；`1` 有 error 级诊断但结果已写出；`2` 致命
（如无像素单元、钻孔跨度未知、**矩形化 `ok=false`**），**不写结果**。
矩形化只要被填平孔洞、丢失岛或形状偏差超精度，即视为输入与精度不相容，
不再输出 CSV，也不会带错导出。

## 9. 验证

### 9.1 合成图案（`tests/test_equiv.py`）

| 图案 | 预期 | 实测 |
|---|---|---|
| 全铜 | kx=ky=kz=k1，ρ=ρ1，cp=cp1 | ✔ |
| 全基底 | kx=ky=kz=k2，ρ=ρ2，cp=cp2 | ✔ |
| 上下各半 | kx=并联、ky=串联（调和）、kz=并联 | ✔ |
| 左右各半 | kx=串联、ky=并联、kz=并联 | ✔ |
| 棋盘 | kx=ky=调和、kz=并联 | ✔ |
| 密度/热容 | 体积加权 / 质量加权 | ✔ |

并验证：任意单元的 `kx`/`ky`/`kz`/`ρ`/`cp` 均为有限正数，且落在该单元所用
`k1`、`k2`（及密度/热容）之间。

边界（`tests/test_equiv.py`）：

- 三层铜均无厚度、Z 同时为 0 时，跨 C1–C2 钻孔**不覆盖 C3**，并记
  `equiv.drill_z_mismatch`（拓扑跨度优先）；
- 计算域只覆盖单元一半时，`domain_coverage≈0.5`、`domain_area_mm2` 为半格，
  且默认出现 `equiv.partial_domain` 告警；
- `k1=NaN/Inf/0/负数` 均记 `materials.invalid` 并退回默认值，输出仍为有限正数；
- 矩形化填平窄孔时 `rectification.ok=false`、`result.fatal=true`，
  等效命令退出码 2 且不写 CSV/临时文件；
- 只覆盖铜的 `k` 时，provenance 逐字段标记 `k=user_global`、
  `density/cp=project_default`。

### 9.2 真实 case

- **`minimal-test-odb`**：JSON 接口往返、CSV+meta 写出、默认材料警告、
  厚度缺失时 `thickness_known=false`。
- **`kicad-kitchen-sink`**：介质层确实收到与其 Z 区间相交的钻孔覆盖。
- **`designodb-rigidflex.tgz`（刚挠结合，43 层，23 个层级 profile）**：
  - step profile 跨整个「刚性 + 柔性」外形（x ∈ [-85.2, 78.15] mm），
    而刚性层各自的层级 profile 只覆盖 x ≥ -4.25 mm；
  - `--px 400 --py 400` 实测刚层单元数 74340、柔层
    （`FLEX_5`/`POLYIMIDE_BASE_11`/`FLEX_6`）90541，柔层单元 x 起点到 -84.94 mm、
    刚层只到 -4.08 mm——**逐层计算域生效**；
  - 每层绝对 Z 取自层叠，柔层与刚层 Z 不同，随 JSON 正确传递；
  - 默认材料警告 `materials.default_used` 出现 2 次（铜、基底各一）；
  - 自动 tolerance = 0.8168 mm（= 2×像素间距），矩形化 `ok=true`
    （`lost` 47.71 mm²、`gained` 37.20 mm²）、强制网格线 51×40、
    91508 个板内单元、19 个等效层各 74340 或 90541 单元，
    无 `cell_without_pixel`、无 error 级诊断；
  - 同一次运行出现 1 条 `rectify.area_warning`（相对误差 2.09%，仍在周长上界内）。

### 9.3 光栅分辨率收敛（`kicad-kitchen-sink`，逐层均值）

| px=py | 单元数/层 | 耗时 | tol (mm) | B.CU kx | B.CU kz | DIELECTRIC_1 kx | F.CU kx |
|---|---|---|---|---|---|---|---|
| 100 | 5989 | 0.1 s | 4.46 | 73.79 | 75.78 | 0.493 | 0.621 |
| 200 | 23756 | 0.4 s | 2.23 | 73.45 | 74.68 | 0.462 | 0.656 |
| 400 | 97000 | 1.7 s | 1.12 | 74.63 | 75.11 | 0.411 | 0.669 |
| 800 | 399457 | 9.5 s | 0.56 | 75.06 | 75.24 | 0.415 | 0.714 |

（耗时为本机库调用（`equivalence_from_document`，**不含** CSV 写出，因为 800 档的
CSV 接近 1 GB）的墙钟时间；`px ≤ 200` 时网格单元内像素不足，CLI 视为致命
`grid.cell_without_pixel` 并拒绝写结果，表中数值取自同一库路径。）

轮廓误差随分辨率的收敛（同一 case，`lost`/`gained`/相对误差/告警）：

| px=py | lost (mm²) | gained (mm²) | 相对误差 | 诊断 |
|---|---|---|---|---|
| 100 | 378.3 | 348.3 | 3.55 % | `rectify.area_warning` |
| 200 | 203.0 | 212.7 | 2.03 % | `rectify.area_warning` |
| 400 | 106.8 | 106.6 | 1.04 % | `rectify.area_warning` |
| 800 | 49.1 | 54.2 | 0.50 % | 无 |

- 铜密集层 `B.CU`：400→800 时 `Δkx=0.44`（0.6%）、`Δkz=0.13`（0.2%），已收敛。
- 介质层 `DIELECTRIC_1`：400→800 时 `Δkx=0.004`（1.0%），已收敛。
- 稀疏铜层 `F.CU`：400→800 时 `Δkx=0.045`（6.7%）仍在变，因为其铜迹线宽度接近像素尺度，
  属于**细特征**的预期慢收敛。结论与 AGENTS 验证要求一致：细走线/小孔需要更高
  `px/py`，**不能**只看 SVG 显示效果判断等效值正确。
- 轮廓误差则随容差按阶梯规律下降（lost/gained 在 400 档起已对称，说明丢的是阶梯本身
  而不是特征）；`px=400` 恰好在 1% 边上，`px=800` 才回到配置上限以内。
  这正是 §2 新增 `rectify.area_warning` 想让人看到的东西。

## 10. 已知近似与限制

1. **阶梯边界**：非轴对齐外形被近似为 tolerance 宽的阶梯；误差在 meta 中量化记录，
   超过固有上界即报错。这是精度与网格规模的显式折衷。
2. **两材料、层内贯通**：层内只有铜/基底两相，并假设沿厚度贯通（推导假设 3）。
   介质层上的非钻孔特征（若存在）被忽略并记 `equiv.dielectric_features_ignored`。
3. **钻孔视作实心铜**：`drill_model=solid_copper`，不区分孔壁镀铜与填铜，
   也不建模孔内空气。
4. **细特征收敛慢**：走线/小孔宽于像素间距时才被稳定解析；`px/py` 需按最小特征
   尺寸选取。
5. **厚度可信度**：层厚缺失时给出 best-effort 绝对 Z 并标 `thickness_known=false`；
   英制铜厚按内置铜密度 8.96 g/cm³ 换算，meta 记录 `thickness_source` 与
   `assumed_density_g_cm3`。
6. **第一版范围**：SOLDERMASK / SOLDER PASTE / 丝印 / 导电胶 / 屏蔽等其他材料层
   暂不套用铜/FR4 模型，逐层给出跳过原因。

可视化产物（掩膜 PNG、热图 PNG 与图例页）的语义、布局、内存与失败处理见
[`02-visualization.md`](02-visualization.md)；本轮由可视化暴露并修掉的矩形化 slab
丢失问题见 [`../fixes/06-rectify-slab-loss.md`](../fixes/06-rectify-slab-loss.md)。

## 11. 复现

```bash
uv sync --all-extras

# 解析器 → preview-2 JSON
uv run --all-extras python -m odbpp export cases/kicad-kitchen-sink \
    -o build/ks --only-json

# 等效器（从 JSON 起）
uv run --all-extras python -m odbpp equivalent --from-json build/ks/PCB.json \
    --px 800 --py 800 -o build/ks-eq

# 或一步到位（输入可以是 ODB++ 目录/压缩包）
uv run --all-extras python -m odbpp equivalent cases/kicad-kitchen-sink \
    --px 800 --py 800 -o build/ks-eq
```

`equivalent` 关键选项：`--px`/`--py`（必填，整板各方向像素数）、
`--nx`/`--ny`（默认等于 `px`/`py`，须满足 `1 ≤ nx ≤ px`）、
`--rectify-tolerance`（默认自动 = 2×像素间距）、`--materials FILE`
（JSON：`global` 与 `layers.<name>` 覆盖）、逐相覆盖
`--copper-k/--copper-density/--copper-cp` 与 `--base-*`、`--warn-partial`、
`--digits`。

可视化相关选项（详见 [`02-visualization.md`](02-visualization.md)）：
`--no-raster-png`、`--raster-scale N`（默认 0 = 自动约 700 px 长边且保持板的长宽比；
N 为强制各向同性 N 倍）、`--no-heatmap`、
`--heatmap-panel-width PX`、`--heatmap-shared-scale`、`--no-gallery`；
已经写出 CSV 后要单独重画热图：

```bash
uv run --all-extras python -m odbpp heatmap build/ks-eq/PCB.MaterialMap.csv \
    -o build/ks-eq/heatmaps --shared-scale
```
