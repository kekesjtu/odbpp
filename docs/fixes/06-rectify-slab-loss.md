# 06 · 矩形化 slab 丢失修复（2026-09-30）

修正对象：为「等效结果可视化」补做真实 case 对照时暴露的矩形化缺陷 ——
**先吸附后配对**使亚容差狭缝把整条竖直 slab 丢掉，缺口又被邻块拉伸覆盖，
全程没有任何诊断。附带两处同源加固与一条新的显式告警。

修正基线：`002af07`（本轮可视化功能的父提交）。
相关功能说明见 [`../equivalence/02-visualization.md`](../equivalence/02-visualization.md)。

## 结论摘要

| # | 问题 | 状态 | 主要验证 |
|---|---|---|---|
| 1 | 亚容差狭缝导致整条 slab 被丢弃，且无诊断 | 已修 | kicad-kitchen-sink `--px 400` 丢失面积 384.65 → 106.80 mm² |
| 2 | 丢弃后相邻 slab 被跨缺口合并，凭空覆盖未测量的轮廓 | 已修 | 只合并首尾相接的 band；新测试用「半宽狭缝」profile 复现 |
| 3 | 细分步长 `xa + step` 与吸附边界相差数 ulp，留下零宽 slab | 已修 | 最后一步直接落在 `key × tolerance` 上 |
| 4 | 仍无法判读的 slab 会被静默跳过 | 已修 | 新增 `rectify.slab_ambiguous`（error）并置 `ok=false` |
| 5 | 大面积轮廓移动只有周长上界兜底，可能无声通过 | 已修 | 新增 `rectify.area_warning`（warning），结果仍被接受 |

全量测试：`694 passed`（基线 691，新增 3 项回归测试）。
真实 case：kicad-kitchen-sink、designodb-rigidflex 均无 error 级诊断；
`--px 400` 两者各出现 1 条 `rectify.area_warning`（详见 §4）。

同一轮真实 case 对照还发现了两类**图本身**的缺陷（不按物理比例绘制），
已单独记在 [`07-picture-aspect.md`](07-picture-aspect.md)。

---

## 1. [P1] 先吸附再配对 → 整条 slab 被丢弃

**根因**（`src/odbpp/rectify.py::_intervals`）：一条扫描线得到一个**有序**交点序列，
按奇偶配对才能读成「实体/空隙」。旧实现在配对**之前**把每个交点吸附到
`round(y / tolerance) * tolerance` 的容差网格上，并去掉相邻重复键：

```python
keys = dedup([snap(y) for y in crossings])
if len(keys) % 2:      # 亚容差狭缝把两个交点并成一个键 -> 奇数
    return None        # 整条 slab 被当成「不可判读」直接丢掉
```

只要某条竖直 slab 里存在一条**比容差还窄**的空隙（真实板上的铣槽残缝、
profile 顶点微小错位、坐标经过单位换算后的浮点尾巴都会造成），两个交点就被并成
一个键，键数变奇数，`_intervals` 返回 `None`，`_bands` 于是整条 slab 不产出矩形。
`_intervals` 在**配对之后**才合并重复区间，因此「先配对、后吸附、再合并」既保留
了狭缝的实体/空隙语义，也不会产生奇数键。

旧实现的静默性来自两处叠加：

1. `_bands` 丢掉 slab 时不报任何诊断；
2. `rectify_region` 的合并步骤只比较区间列表，`merged[-1] = (merged[-1][0], x1, …)`
   把**跨过缺口**的下一块直接接上，于是邻块的 y 区间被拉伸覆盖到没人测量过的
   x 范围 —— 该填的没填、该留的没留，还看起来「面积大致对」。

**复现（kicad-kitchen-sink，`--px 400 --py 400`，容差 1.1155 mm）**：

| 量 | 旧 | 新 |
|---|---|---|
| 矩形数 | 196 | 198 |
| lost | **384.65 mm²** | 106.80 mm² |
| gained | 98.24 mm² | 106.59 mm² |
| shape deviation | 482.89 mm² | 213.39 mm² |
| 相对误差 | 2.36 % | 1.04 % |
| 被丢弃的 slab | **3 条**（各宽 1.1155 mm） | 0 |
| 诊断 | 无 | `rectify.area_warning` |

旧结果 `ok=true`：检查用的上界是
`allowed = max(0.5, 1% × 20477.07, 周长 741.38 × 1.1155) = 827.01 mm²`，
482.89 < 827.01 就通过了。也就是说**丢失了 385 mm² 板面（整板的 1.88%）却完全无声**，
只因为大板粗容差的周长上界足够宽松。

**修改**（`_intervals`）：先配对、再吸附、再合并相邻重复区间。

```python
intervals.append((_snap_key(y0, tolerance), _snap_key(y1, tolerance)))
# … 吸附之后才合并 keys[i][1] == keys[i+1][0] 的相邻区间
```

## 2. [P1] 丢弃 slab 后跨缺口合并

**根因**：见上 §1 第 2 点。缺一条 slab 时，邻块被拉伸成一条跨越缺口的矩形，
把「没测量到」当成「有实体」。

**修改**：只合并**首尾相接**的 band：

```python
if (merged and merged[-1][2] == intervals
        and abs(merged[-1][1] - x0) <= 1e-9 * max(1.0, abs(x0))):
```

**验证**（`tests/test_rectify.py::test_a_sub_tolerance_void_sliver_does_not_drop_the_slab`）：
20×20 实体，左半被一条 0.001 mm 狭缝切开（容差 1 mm）。旧实现丢掉左半 slab，
又把右半拉伸过去；新实现得到一整块 400 mm²、`lost < 1 mm²`、`ok=true`。

## 3. [P2] 细分步长与吸附边界相差数 ulp

**根因**：斜边 slab 按 `count = ceil(width / tolerance)` 细分，步长用
`width / count`，端点写成 `xa + step`。浮点累加使最后一个端点与相邻 slab 的
`key × tolerance` 边界相差数 ulp，于是留下一条零宽 slab（其区间列表虽非空，
但矩形面积为零，且合并条件不再成立）。

**修改**：最后一步直接用吸附边界 `xb = x1 if index == count - 1 else xa + step`。
`_bands` 返回的 band 因此首尾严格相接（§2 的合并也因此更稳）。

## 4. [P2] 仍不可判读的 slab 必须报错

配对优先之后，奇数交点只可能来自自交/退化输入。这种 slab 依然会被跳过，
但**不再无声**：新增 `rectify.slab_ambiguous`（error 级），消息给出 slab 的
x 区间与所用容差，并把 `ok=false`（有片段根本无法表达，不是阶梯误差）。

验证：`_intervals([1.0, 2.0, 3.0], 1.0) is None`（奇数交点）与新诊断路径由
`tests/test_rectify.py::test_crossings_are_paired_before_they_are_snapped` 固定。

## 5. [P2] 周长上界内的轮廓移动改为显式告警

**根因**：`allowed = max(面积下限, 相对上限, 周长 × 容差)`。周长上界是容差本身的
几何精度极限，在大板 + 粗容差下可能远宽于配置的 1% 相对上限，于是「丢了 2.4%
的板面」也能通过。

**修改**：结果仍在周长上界内、但已超过配置上限时记
`rectify.area_warning`（warning），消息给出移动面积、占比、配置上限与周长上界。
判据不变（仍接受，仍写 CSV），但绝不再无声。

验证（`test_a_loose_perimeter_bound_still_reports_the_area_moved`）：
80 mm 直角三角形的 45° 斜边在 1 mm 容差下移动 40 mm²，超过相对上限 32 mm²、
低于周长上界 273 mm² → `ok=true` 且出现告警；同尺寸正交矩形不出现告警。

真实 case 表现（`--px 400`）：

| case | 容差 | 相对误差 | 告警 | error |
|---|---|---|---|---|
| kicad-kitchen-sink | 1.1155 mm | 1.04 % | 有 | 无 |
| designodb-rigidflex | 0.8168 mm | 2.09 % | 有 | 无 |
| kicad-kitchen-sink `--px 800` | 0.5577 mm | 0.50 % | 无 | 无 |

这条告警正好把「粗光栅下轮廓误差偏大」变成可核对的信息，
配合 `docs/equivalence/01-material-equivalence.md` §9.3 的收敛表看。

## 6. 影响与文档同步

- `docs/equivalence/01-material-equivalence.md` §9.2/§9.3 的实测数字按修复后重测
  更新（每层单元数、时间、`lost/gained`、收敛结论）。
- 可视化侧的浅灰竖带消失：修复前后同一 `--px 400` 的热图对照见
  [`../equivalence/02-visualization.md`](../equivalence/02-visualization.md) §8。
- 输出契约未变（CSV 列、meta 字段、`ok` 语义都不变），只是数值更接近真轮廓。
