# 审阅问题修正记录（2026-09-21）

修正对象：[06-project-review-2026-09-21.md](../reviews/06-project-review-2026-09-21.md) 的七项已确认问题。
修正基线：`8dd81ae`（审阅报告入库时的提交）。本文记录**修了什么、如何验证、还剩什么**；
审阅报告本身保持原样，作为问题清单与复现脚本的出处。

## 结论摘要

| # | 优先级 | 问题 | 状态 | 主要验证 |
|---|---|---|---|---|
| 1 | P1 | 介质实体未扣除相交钻孔投影 | 已修 | 四例 `crossing_drill_overlap_mm2` 全为 0 |
| 2 | P1 | JSON 缺层叠信息、属性单位作用域丢失 | 已修 | `attribute_units` / `thickness_mm` / `z_span` / `copper_weight` |
| 3 | P1 | 整圆弧被静默丢弃 | 已修 | 面积 31.386 mm²（理论 10π≈31.416，差值为离散化） |
| 4 | P1 | 输出取整后未复核拓扑 | 已修 | 四例 `invalid_ring_count` 全为 0 |
| 5 | P2 | `intersection` 空集与多参数语义错误 | 已修 | 空集→0；三区域→0 |
| 6 | P2 | 基础安装漏声明 pyclipper | 已修 | `pyclipper` 提升为核心依赖 |
| 7 | P2 | `--no-diagnostics` 未生效于 JSON | 已修 | 文件内 `diagnostics` 键消失 |

全量测试：`520 passed`（`conda run -n odbpp python -m pytest -p no:cacheprovider`）。
四例完整导出退出码均为 0，error 级诊断与 `failed_layers` 均为空。
证据由 `tools/review_current_project.py` 重新生成于 `out/review-evidence.json`。

---

## 1. 介质实体扣除相交钻孔投影

**根因**：`expand_layer()` 对介质层只用 profile 填充并叠加本层图元，既不读钻孔层、
也不判断 Z 跨度。且 `features` 为空时提前返回，恰恰是真实介质层最常见的情形。

**修正**：

- 新增 `src/odbpp/stackup.py`：`Stackup` 以 matrix 行序把铜层编成 `0..n-1` 的**平面序号**，
  介质层占用半开区间 `(i, j)`（相邻铜层；若 `CU_TOP`/`CU_BOTTOM` UID 可解析则优先采用），
  钻孔层由 `START_NAME`/`END_NAME` 解析为 `(a, b)`。重叠判定即普通区间相交 ——
  半开区间保证终止于平面 `b` 的盲孔不会穿透 `b` 以下的介质。
- `expand_layer()` 在叠加本层图元**之后**，对介质层扣除 `Stackup.drills_through()` 的并集
  （`src/odbpp/expand.py:_subtract_drills`）。钻孔区域按 `(step, layer)` 在 `Expander`
  内缓存，多个介质层共享同一次钻孔合成。
- 跨度无法解析时**不扣除**并记录 `stackup.drill_span_unknown`；介质层无法定位上下铜层时
  记录 `stackup.dielectric_span_unknown`。不猜测“整板贯穿”。
- `ROUT` 不参与该投影（AGENTS 约束 3 针对钻孔图元；`ROUT` 是铣切轮廓）。

**验证**（`out/review-evidence.json` 的 `crossing_drill_overlap_mm2`）：刚挠板的通孔、
盲孔、埋孔按 Z 区间正确分配 —— 例如 `DIELECTRIC_3` 仅扣除 `D_1_2`+`D_1_10`，
`DIELECTRIC_5` 仅扣除 `D_2_3`+`D_1_10`，`POLYIMIDE_BASE_11`（挠性区）额外扣除 `D_5_6`；
所有应相交组合的残余平面重叠均为 0。

**残留**：证据脚本同时保留 `drill_xy_overlap_mm2`（忽略 Z 的纯投影重叠），
该值本来就不为零；判定实体正确请使用 `crossing_drill_overlap_mm2`。

## 2. 层叠信息与属性单位作用域

**根因**：`parse_attrlist()` 返回的单位作用域被 `_load_layer()` 丢弃；导出只写原始字符串。

**修正**：

- `Layer.attribute_scope` / `Step.attribute_scope` 记录 attrlist 的实际单位作用域
  （文件声明 `UNITS` 用它，否则继承 job 默认）。
- `Stackup.thickness()` / `Stackup.weight()` 给出**规范化解释**：

  | 字段 | 语义 | 输出 |
  |---|---|---|
  | `.layer_dielectric` | 坐标单位制下的层厚 | `thickness_mm` + `thickness{raw,value_mm,source,attribute_units}` |
  | `.copper_weight` | `MM`→µm 铜厚；`INCH`→oz/ft² 面密度 | `copper_weight{value,kind,unit[,thickness_mm]}` |

- `.layer_dielectric` 出现在铜层上时按 AGENTS 注 5 报 `attr.layer_dielectric_on_copper`，
  并在 `thickness.spec_deviation=true` 标注（值仍给出，因为它确实是长度）。
- 每层 JSON 新增 `attribute_units`、`thickness_mm`、`thickness`、`copper_weight`、
  `copper_index`、`z_span`、`start_layer`、`end_layer`；step 级新增 `stackup` 数组
  （行序、平面序号、Z 区间、厚度），使文档可独立重建叠层。

**验证**：`designodb-rigidflex` 的 `DIELECTRIC_3` 由 `UNITS=INCH` +
`.layer_dielectric=0.00255906` 得到 **0.065000124 mm**（审阅报告中的期望值），
而文档顶层仍标注 `units: mm` 仅用于多边形坐标。无厚度声明的介质层（如 `kicad-video-odb`）
输出 `thickness_mm: null` 并报 `stackup.thickness_unknown`。

## 3. 整圆弧

**根因**：`_path_points()` 对 `start == end` 的整圆返回两个重合点，去重后不足两点而被丢弃。

**修正**：删除该特例，统一走 `geometry.arc_points()` —— 它本身已按规范 p.40 把起终点重合
展开为 360°。仅保留“圆心与端点重合（半径为零）”的退化判定与 `geometry.arc_degenerate`。

**验证**：`c=(0,0)`、起终点 `(5,0)`、`r1000`（MM，直径 1 mm）得到理论环带
`π(5.5²-4.5²)=10π≈31.4159 mm²`；实测 31.386 mm²（差值来自圆弧离散化），无诊断。

## 4. 输出精度下的拓扑复核

**根因**：内部 1e-6 mm 的几何被逐点四舍五入到 1e-4 mm 后直接序列化，相邻点合并、
窄环压扁、边相交都不会被重新检出；`area_mm2` 仍来自取整前几何。

**修正**：新增 `clipper.snap_to_grid(region, digits)`：

1. 顶点按 `10**-digits` mm 网格取整，并去除**非相邻**重复顶点（Clipper 会直接拒绝这类路径：
   刚挠板 `SPT` 第 2183 环即此情形）；
2. 以 NONZERO 填充规则重新 union，按 spec p.39 重新定岛/孔与绕向；
3. 每个轮廓再过一次 `SimplifyPolygon`，把 Clipper 可能产生的**弱简单**轮廓
   （两点相接、访问同一顶点两次）拆成严格简单环 —— 这是 `ASSEMT` 层取整后出现 32 个
   自相交环的原因；
4. 在目标分辨率下无面积的环被丢弃并计入诊断 `export.degenerate_ring`。

导出流水线（`export.iter_step_previews`）在判定几何结果前调用它，`area_mm2` 与 `bounds_mm`
因此来自**最终写入坐标**。`PreviewOptions.round_digits` 的语义由“打印精度”升级为
“模型分辨率”，文档字符串已同步。

**验证**：四例 `invalid_ring_count` 由 6100（刚挠板）降为 0；`minimal-test-odb`、
`kicad-kitchen-sink`、`kicad-video-odb` 亦为 0。刚挠板仍有 11 条
`export.degenerate_ring`（1e-4 mm 下确实无面积的环），属显式诊断而非静默丢弃。
JSON 体积随之下降：刚挠板 95.9 MB（原约 126 MB）、HTML 25 MB（原约 34 MB）。

## 5. `intersection` 语义

**根因**：函数先过滤空区域，使 `A ∩ ∅` 返回 `A`；三个以上区域时把后续区域一起塞进
clip 参数，实际计算 `A ∩ (B ∪ C)`。

**修正**：改为逐对求交；首个操作数为空即返回空，任一操作数为空立即短路返回空。

**验证**：`intersection(A, ∅)`、`intersection(∅, A)`、`intersection(A, B, C)`
（`B`/`C` 为 `A` 的两半）均为 0；`intersection(A, B, B)` 面积 2 mm²。

## 6. pyclipper 依赖声明

**根因**：`dependencies = []`，pyclipper 只列在可选 extra，而 `odbpp/__init__.py`
无条件导入几何层，故 `pip install -e .` 得到的包连 `import odbpp` 都会失败。

**修正**：`pyclipper` 移入 `project.dependencies`；`geometry` extra 仅保留
`shapely`/`numpy`（测试与工具使用，运行时不需要）。新增 `tests/test_packaging.py`
从 `pyproject.toml` 读取声明，防止回退。

## 7. `--no-diagnostics`

**根因**：参数只影响终端错误打印，未传给 `step_to_json(include_diagnostics=...)`。

**修正**：`SvgOptions`/`write_outputs()` 增加 `include_diagnostics` 形参，
CLI 传入 `not args.no_diagnostics`。

**验证**：导出最小样本后 JSON 中不再存在 `diagnostics` 键；默认导出仍包含。

---

## 新增/变更的 JSON 字段（schema 仍为 `odbpp/preview-1`）

```jsonc
// step 级
"stackup": [ { "row": 6, "name": "DIELECTRIC_1", "type": "DIELECTRIC",
               "copper_index": null, "thickness_mm": 1.51,
               "z_span": {"from_layer": "F.CU", "to_layer": "B.CU",
                          "copper_index": [0, 1]},
               "start_layer": null, "end_layer": null } ]
// layer 级新增
"attribute_units": { "system": "INCH", "origin": ".../attrlist" },
"thickness_mm": 0.0650001,
"thickness": { "value_mm": 0.065000124, "raw": 0.00255906,
               "source": ".layer_dielectric", "attribute_units": "INCH" },
"copper_weight": { "value": 0.875563, "kind": "areal_density_oz_ft2",
                   "unit": "oz/ft2" },
"start_layer": "SIGNAL_1", "end_layer": "SIGNAL_10",
"copper_index": 0,
"z_span": { "from_layer": "SIGNAL_1", "to_layer": "SIGNAL_10",
            "copper_index": [0, 9] },
"drills_subtracted": ["D_1_2", "D_1_10"],
"drill_cut_mm2": 28.3517
```

新增诊断码：`stackup.dielectric_span_unknown`、`stackup.drill_span_unknown`、
`stackup.thickness_unknown`、`attr.layer_dielectric_on_copper`、`export.degenerate_ring`。

## 验证方法

```powershell
# 全量测试
conda run -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/pytest-fix

# 四例完整导出（与审阅报告同一命令模板）
conda run -n odbpp python -m odbpp export cases/minimal-test-odb        -o out/minimal-test-odb        --per-layer-svg
conda run -n odbpp python -m odbpp export cases/kicad-kitchen-sink      -o out/kicad-kitchen-sink      --per-layer-svg
conda run -n odbpp python -m odbpp export cases/kicad-video-odb.tgz     -o out/kicad-video-odb.tgz     --per-layer-svg
conda run -n odbpp python -m odbpp export cases/designodb-rigidflex.tgz -o out/designodb-rigidflex.tgz --per-layer-svg

# 重新生成审阅证据（含整圆、intersection、取整流水线、无诊断标志、逐例有效性）
conda run -n odbpp python tools/review_current_project.py
```

`tools/review_current_project.py` 已增强：`rounding` 同时记录“逐点取整仍不安全”与
“流水线已量化并丢弃退化环”；介质重叠除忽略 Z 的投影重叠外，另按 JSON 的 `z_span`
计算**真正相交**的钻孔集合。

## 未覆盖与残留风险

- **未与独立 CAM 软件做逐层像素/几何比对**，也未做浏览器交互验收；测试通过仍不等于
  实体正确性验收通过。孔壁/铜箔优先级等物理语义未在此模型中表达。
- **Z 区间以铜层序号表达**，不是毫米坐标：本模型不臆造铜厚与介质绝对 Z。需要绝对厚度
  时可组合 `stackup[].thickness_mm`（仅介质/掩膜/锡膏，或带 `spec_deviation` 的铜层值）。
- 钻孔一律按几何扣除，**未区分金属化/非金属化**（`tools` 的 `TYPE` 已知但本阶段未用于
  实体语义）；`ROUT` 不参与扣孔。
- `kicad-video-odb` 的介质层 attrlist 为空，厚度只能输出 `null` 并报诊断；这是数据缺失，
  不是转换错误。
