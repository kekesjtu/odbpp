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
[ 5. MaterialMap.csv + meta.json ]
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
  某些子条带窄于 tolerance、网格被迫过密」的问题。
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
kx = 1 / mean_rows( 1 / mean_cols_in_row( k ) )      # 行内串联、行间并联
ky = 1 / mean_cols( 1 / mean_rows_in_col( k ) )      # 列内串联、列间并联
kz = φ·k1 + (1-φ)·k2                                 # 柱间并联
ρ  = φ·ρ1 + (1-φ)·ρ2                                 # 体积分数加权
cp = ( φ·ρ1·cp1 + (1-φ)·ρ2·cp2 ) / ρ                 # 质量加权
```

黑/白由该层的正向实体图案与该层计算域共同决定：

- **域外**：该层在此单元不存在，不生成单元，绝不当作 FR4。
- **域内**：图案覆盖处为铜（黑），其余为基底（白）。

### 6.1 钻孔规则

与某物理层 Z 区间相交的钻孔，其**投影**在该层内视为**全铜、全黑**（`drill_model =
solid_copper`）。规则对铜层与介质层同样适用：先在黑/白掩膜上叠加钻孔，
再统计 φ。绝不会把钻孔当空气，也不会重复扣除已从介质多边形中扣掉的孔。
钻孔跨度无法确定时报 `equiv.drill_span_unknown`（致命），不擅自视为贯穿全板。

## 7. 输出

### 7.1 `*.MaterialMap.csv`

自描述表头，每个量都带单位。列（`CSV_COLUMNS`）：

```text
layer, layer_type, region_id, cell_i, cell_j,
cell_x0_mm, cell_y0_mm, cell_x1_mm, cell_y1_mm,
z_start_mm, z_end_mm, z_thickness_known,
copper_area_fraction, kx_w_mk, ky_w_mk, kz_w_mk,
density_kg_m3, specific_heat_j_kgk,
pixel_rows, pixel_cols, pixel_count, partial_domain
```

该布局是本项目自己的设计：参考 `docs/Flotherm_reference_MaterialMap.csv`
看了「需要表达什么」，没有照抄，也不以 Flotherm 可导入为目标。

### 7.2 `*.MaterialMap.meta.json`（`odbpp/materialmap-1`）

CSV 无法表达或不宜逐行重复的信息：

- `units`：长度/面积/k/ρ/cp 的统一单位（一律 mm、mm²、W/(m·K)、kg/m³、J/(kg·K)）；
- `z_axis`：绝对 Z 方向约定（自顶面向下）；
- `drill_model`：固定 `solid_copper`；
- `raster`：`px`/`py`/`pixel_step_mm`/`board_bounds_mm`/`sampling`；
- `grid`：`nx`/`ny`/强制线与总数/`region_count`/`cell_count`/网格线坐标；
- `rectification`：`tolerance_mm`/原始与矩形化面积/`lost`/`gained`/`net`/
  `shape_deviation`/`relative_area_error`/`ok`/矩形清单；
- `materials`：`defaults`（数值+来源）与 `by_layer`（每层实际取值及来源）；
- `layers[]`：每层 `status`（`equivalent`/`skipped`/`failed`）、`reason`、
  `domain_id`、`domain_source`、`z_mm`、`drill_overlays`、`cell_count`；
- `diagnostics[]`：稳定 code 的诊断列表（level/code/message/path）。

## 8. 诊断 code

| code | 含义 |
|---|---|
| `rectify.empty_profile` | 轮廓为空，无法矩形化 |
| `rectify.no_rectangles` | 矩形化后无 region |
| `rectify.area_error` | 形状偏差超过精度允许的上界 |
| `rectify.island_lost` | 某岛窄于 tolerance，被丢弃 |
| `rectify.hole_filled` | 某孔窄于 tolerance，被填平 |
| `grid.forced_lines_overflow` | 强制边界数超过用户指定格数 |
| `grid.no_board_cells` | 网格内没有板内单元 |
| `grid.cell_without_pixel` | 网格单元内无像素中心（致命） |
| `equiv.unsupported_schema` | 非 preview-2（抛 `EquivalenceError`，非诊断） |
| `equiv.layer_failed` | 某层无法等效 |
| `equiv.layer_skipped` | 某层按规则跳过（附原因） |
| `equiv.missing_domain` | 引用的计算域不存在 |
| `equiv.drill_span_unknown` | 钻孔 Z 跨度无法确定（致命） |
| `equiv.layer_z_unknown` | 层 Z 区间缺失，只能 best-effort |
| `equiv.partial_domain` | 计算域只覆盖部分 region |
| `equiv.layer_empty` | 该层在其计算域内没有任何实体 |
| `equiv.dielectric_features_ignored` | 介质层上非钻孔特征被忽略 |
| `materials.default_used` | 使用了项目默认材料 |
| `materials.invalid` | 材料字段非法，已退回默认值 |

退出码：`0` 无 error 级诊断；`1` 有 error 级诊断但结果已写出；`2` 致命
（如无像素单元、钻孔跨度未知），**不写结果**。

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

### 9.2 真实 case

- **`minimal-test-odb`**：JSON 接口往返、CSV+meta 写出、默认材料警告、
  厚度缺失时 `thickness_known=false`。
- **`kicad-kitchen-sink`**：介质层确实收到与其 Z 区间相交的钻孔覆盖。
- **`designodb-rigidflex.tgz`（刚挠结合，43 层，23 个层级 profile）**：
  - step profile 跨整个「刚性 + 柔性」外形（x ∈ [-85.2, 78.15] mm），
    而刚性层各自的层级 profile 只覆盖 x ≥ -4.25 mm；
  - 实测刚层单元数 73563、柔层（`FLEX_5`/`POLYIMIDE_BASE_11`/`FLEX_6`）89764，
    柔层单元 x 起点到 -85.00 mm，刚层只到 -4.00 mm——**逐层计算域生效**；
  - 每层绝对 Z 取自层叠，柔层与刚层 Z 不同，随 JSON 正确传递；
  - 默认材料警告 `materials.default_used` 出现 2 次（铜、基底各一）；
  - 自动 tolerance = 0.8168 mm（= 2×像素间距），矩形化 `ok=true`，
    强制网格线 51×40，无 `cell_without_pixel`。

### 9.3 光栅分辨率收敛（`kicad-kitchen-sink`，逐层均值）

| px=py | 单元数 | 耗时 | tol (mm) | B.CU kx | B.CU kz | DIELECTRIC_1 kx | F.CU kx |
|---|---|---|---|---|---|---|---|
| 100 | 5912 | 0.1 s | 4.46 | 71.82 | 73.83 | 0.495 | 0.625 |
| 200 | 23190 | 0.4 s | 2.23 | 72.48 | 73.71 | 0.466 | 0.665 |
| 400 | 95913 | 1.4 s | 1.12 | 75.05 | 75.49 | 0.412 | 0.673 |
| 800 | 399457 | 7.4 s | 0.56 | 75.06 | 75.24 | 0.415 | 0.714 |

- 铜密集层 `B.CU`：400→800 时 `Δkx=0.012`（0.02%）、`Δkz=0.25`（0.3%），已收敛。
- 介质层 `DIELECTRIC_1`：400→800 时 `Δkx=0.002`（0.5%），已收敛。
- 稀疏铜层 `F.CU`：400→800 时 `Δkx=0.041`（6%）仍在变，因为其铜迹线宽度接近像素尺度，
  属于**细特征**的预期慢收敛。结论与 AGENTS 验证要求一致：细走线/小孔需要更高
  `px/py`，**不能**只看 SVG 显示效果判断等效值正确。

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
