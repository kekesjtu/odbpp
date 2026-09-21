# 第三次审阅问题修正（2026-09-21）

修正对象：[10-third-review-2026-09-21.md](10-third-review-2026-09-21.md) 的三项 P2。
基线：`66bb1d4`。本文记录**改了什么、如何验证、还剩什么**；审阅报告与复现脚本保持原样。

## 结论摘要

| # | 优先级 | 问题 | 状态 | 主要验证 |
|---|---|---|---|---|
| 1 | P2 | 缺厚度的非结构实体层被打成 `thickness_known=true` | 已修 | `F.PASTE`（2 环、无厚度）自身 `false`，其下 `F.CU` 仍 `true` |
| 2 | P2 | 默认写出的 SVG/HTML 丢失预览精度 | 已修 | 6 位预览默认导出：JSON 与 SVG/HTML 均保留 `x=0.00002`；显式不同精度时重新规整 |
| 3 | P2 | 改变 JSON 精度后 step 汇总仍描述旧模型 | 已修 | 4 位下 `polygon_count=0`、`empty_layers=["top"]`、`bounds_mm=null`，与层数组一致 |

全量测试：`548 passed`（较上次 `541` 新增 7 项回归用例）。三项审阅反例经
`tools/review_third_pass.py` 复跑后均输出修正后的值。

---

## 1. 非结构层「位置已知」与「自身厚度已知」被混为一谈

**根因**（`src/odbpp/stackup.py`）：`_build_z_table()` 只有一个累积标志 `known`。
结构性层缺厚度时置 `false` 并向下传播；而非结构层（丝印/锡膏等）缺厚度时**不改**
`known`，于是该层自身假定的零厚度被直接标成「已知」，违反 AGENTS 第 8 条
「厚度数据缺失时显式标记 `thickness_known=false`」。

**修正**：两个问题分开维护。

- `position_known`：累计位置是否只由已声明厚度支撑。只有**结构性**层（铜、介质、阻焊）
  缺厚度才置 `false`，装饰层不改变铜面位置，因而**不污染**后续层；
- 每层的 `thickness_known = stated and position_known`：装饰层缺厚度时自身为 `false`，
  其下方的铜层仍为 `true`。

`ZExtent.thickness_known` 与模块文档同步改写，明确它同时描述「本层厚度是否有来源」与
「位置是否可靠」。`_material_thickness()` 的返回值（`stated`）无需改动。

**验证**（`tools/review_third_pass.py` 的 `missing_paste_thickness`）：

```json
{ "name": "F.PASTE", "polygon_count": 2, "thickness_mm": null,
  "z_mm": { "start_mm": 0.0, "end_mm": 0.0, "thickness_mm": 0.0,
            "thickness_known": false, "source": "unknown" } }
```

新增/调整测试（`tests/test_stackup.py`）：

- `test_a_decorative_layer_does_not_take_the_stack_down_with_it`：改为断言丝印/锡膏**自身**
  `false`、其下 `SIGNAL_1` 仍 `true`（原用例把两层语义混在一起）；
- `test_a_stated_decorative_thickness_is_known`：声明了钢网厚度的锡膏层为 `true`。

`tests/test_export.py::test_a_decorative_layer_with_geometry_but_no_thickness_says_so`
用真实案例 `kicad-kitchen-sink` 覆盖「有实体、缺厚度的锡膏 + 其下铜层」。

## 2. 三种产物共享同一最终分辨率与规整后的几何

**根因**：`write_outputs()` 已让 JSON 跟随 `preview.round_digits`，但 SVG 仍无条件用
`SvgOptions()` 的默认 4 位；`render_svg()` 直接逐点格式化原始 rings，既没有继承默认值，
显式指定不同精度时也不重新规整。0.00002×1 mm 的合法矩形因此被画成
`M0 0L0 0L0 1L0 1Z`（宽度为零），而 JSON 仍报告其面积。

**修正**：

- `src/odbpp/export.py` 新增 `regularise_preview(preview, round_digits)`：在目标网格上
  **重新规整每个层的区域**（复用 `snap_to_grid`，可解决自交、合并碎条、重推岛/孔嵌套），
  重算 `area_mm2`/`bounds`/`empty_layers` 并记录分辨率；已在目标分辨率上的预览原样返回，
  常规路径零开销。丢环继续记 `export.degenerate_ring`。
- `SvgOptions.round_digits` 由 `int = 4` 改为 `Optional[int] = None`：`None` 表示
  **继承预览分辨率**；显式给值则以该值重新规整，而非对既有坐标做舍入。
- `render_svg()` / `render_html()` 各自先解析分辨率并规整一次，再绘制、再生成元数据
  （HTML 页眉的环数/边界、图层行、内联 SVG 同源）。
- `write_outputs()`：JSON 走 `preview_options` 或预览分辨率，绘图走 `svg_options` 或
  跟随 JSON；两者一致时复用同一个规整结果，一次写出只规整一次。

**验证**：6 位预览默认导出后，JSON 保留 `x=0.00002`，SVG 路径为
`M0.00002 0L0 0L0 1L0.00002 1Z`，HTML 内联同一路径；显式
`SvgOptions(round_digits=2)` 时 SVG 的 `data-rings=0`、`data-area=0`、`d=""`，
JSON 仍为 6 位。

新增测试（`tests/test_svg.py::TestOutputPrecisionConsistency`）直接读取
`write_outputs()` 生成的 JSON、SVG、HTML 三者对照，而非只查 JSON。

## 3. step 汇总与边界从「实际写出的层」派生

**根因**（`src/odbpp/export.py`）：`step_to_json()` 的 `bounds_mm`、
`summary.polygon_count`、`summary.empty_layers` 取自原始 preview，而 `layer_to_json()`
在低精度请求下已重新规整并删除退化环。汇总因此描述一个不再写出的模型：
层内 `polygons=[]`、`area_mm2=0`，summary 却是 `polygon_count=1`、`empty_layers=[]`。

**修正**：

- `step_to_json()` 先按最终分辨率**规整一次**（`regularise_preview`），再生成全部层数据；
- step 的 `bounds_mm`、`summary.polygon_count`、`summary.empty_layers` 一律从**已生成的
  层数组**派生（边界取各层 `bounds_mm` 的并集），不再读原始 preview；`failed_layers`
  仍来自 preview（它是「无法合成」而非「被规整掉」）；
- `stackup[]` 也从同一规整视图生成，避免多处重复规整。

**验证**：审阅用例以 6 位预览、`round_digits=4` 输出后：

```json
"layers": [{ "polygons": [], "area_mm2": 0.0, "bounds_mm": null }],
"steps": [{ "bounds_mm": null }],
"summary": { "polygon_count": 0, "empty_layers": ["top"], "failed_layers": [] }
```

新增测试（`tests/test_export.py`）：低精度下汇总/边界与层数组一致；高精度请求
（4 位预览 → 6 位输出）下 `polygon_count`、`empty_layers`、`bounds_mm` 与层数组一致。

## 未覆盖与残留风险

- **未重新导出 `out/` 四例，也未做浏览器交互核对**：本文只保证代码路径与新增回归用例；
  `out/` 属派生产物且不入库，下次完整导出会自动带上本轮修正。
- **step 级 `profile.polygons` 仍逐点取整**（`_profile_json`），未走 `snap_to_grid`：
  profile 是板框单一闭合轮廓，退化风险远低于特征层，本轮未纳入；若出现退化板框需另行处理。
- **Z 语义细化**：`09-review2-fixes` §4 中「丝印/锡膏厚度缺失不影响整叠层」的表述仍然成立，
  但需与本文 §1 合读——**该层自身**的 `thickness_known` 由本轮起为 `false`。
- 结构性层集合、铜密度假设、局部叠层等 `09` 已列残留限制不变。

## 验证方法

```powershell
# 全量测试
conda run -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/pytest

# 复现三问题（现应输出修正后的值）
conda run -n odbpp python tools/review_third_pass.py

# 旧用例回归
conda run -n odbpp python tools/review_followup.py
```

## 相关提交

- `fix(stackup)`：分开维护层位置可靠性与本层厚度来源；
- `fix(export)`：`regularise_preview`，step 汇总/边界从写出的层派生；
- `fix(svg)`：JSON/SVG/HTML 共享最终分辨率并重新规整。
