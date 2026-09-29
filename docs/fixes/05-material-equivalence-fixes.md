# 05 · 物性等效审阅问题修复（2026-09-29）

修正对象：本轮针对物性等效器（数据流第 4-5 步）的边界样例审阅，6 项问题
（P1×4、P2×2），外加「实现文档引用的本地文件未被 Git 跟踪」一项。
修正基线：`c0a6b34`。本文记录**根因、修改、验证**；审阅本身仅执行与复现，未改文件。

## 结论摘要

| # | 优先级 | 问题 | 状态 | 主要验证 |
|---|---|---|---|---|
| 1 | P1 | 层厚缺失时钻孔可能覆盖错误的层 | 已修 | 三层 Z 全为 0 时跨 C1–C2 钻孔不再覆盖 C3，并报 `equiv.drill_z_mismatch` |
| 2 | P1 | 部分计算域按整格输出材料 | 已修 | 新增 `cell_area_mm2`/`domain_area_mm2`/`domain_coverage` 列与默认告警、meta 语义 |
| 3 | P1 | 非有限材料输入生成无效结果 | 已修 | `NaN`/`Inf`/`0`/负数统一记 `materials.invalid` 并退回默认值，输出恒为有限正数 |
| 4 | P1 | 矩形化失败后仍写出 CSV | 已修 | `rectification.ok=false` 即致命，等效命令退出码 2 且不留 CSV/临时文件 |
| 5 | P2 | 材料来源未按字段记录 | 已修 | provenance 逐字段记 `value`/`origin`/`source`，未覆盖字段单独告警 |
| 6 | P2 | 实现文档导热公式写错 | 已修 | 改为 `kx = mean_rows(1/mean_cols_in_row(1/k))`，`ky` 对称 |
| — | — | 实现文档引用的本地文件未跟踪 | 已修 | `git add` `AGENTS.md`、两份推导文档、参考 CSV |

全量测试：`653 passed`（基线 640，新增 13 项边界测试）。
真实 case `designodb-rigidflex.tgz`（200×200 光栅）无 error 级诊断、无钻孔
误匹配告警，`domain_coverage` 全为 1.0。

---

## 1. [P1] 钻孔按拓扑跨度投影，绝对 Z 仅作回退

**根因**：`equiv._overlaps()` 只比较绝对 Z。当某层格式未给出厚度时，其
best-effort `z_mm` 可退化为 `start_mm == end_mm == 0`；多个这样的层落在同一
点，因而任何钻孔都会与它们“相交”。复现中跨 C1–C2 的钻孔因此把 C3 也标成
全铜，且没有任何诊断。

**修改**（`src/odbpp/equiv.py`）：

- `_Drill` 增加 `copper_span`，由 stackup 行的 `z_span.copper_index` 解析
  （`_copper_span()`）。
- 新增 `_topological_overlap()`：有跨度时按铜层序号判定——铜层是平面，按
  **闭区间**包含端点；介质层是平面间薄片，按**半开区间**相交。二者都优先于
  绝对 Z。
- 层厚未知且拓扑与 Z 结论不一致时记 `equiv.drill_z_mismatch`（以拓扑为准）；
  文档没有跨度、只能退回绝对 Z 且层厚未知时记 `equiv.drill_match_z_fallback`。
- 跨度完全无法确定仍按原逻辑记 `equiv.drill_span_unknown`（致命）。

**验证**（`tests/test_equiv.py::TestDrillProjection::
test_a_drill_spanning_two_planes_does_not_overlay_a_third`）：三层铜层
`copper_index=0/1/2`、Z 均为 0、厚度未知，钻孔跨度 `[0,1]`。结果 C1/C2 收到
钻铜、C3 的 `copper_fraction` 全为 0，并出现 `equiv.drill_z_mismatch`。
真实刚挠板未出现任何误匹配告警。

## 2. [P1] 部分计算域显式输出覆盖率

**根因**：`_evaluate_layer()` 只用域内像素统计物性，却把 `cell_x0/y0/x1/y1`
写成完整共享网格单元；`partial_domain=1` 只表明“部分覆盖”，没有表达实际
体积。复现中材料仅占 10×10 mm 单元的一半，CSV 仍给出整格尺寸与完整 FR4
密度，下游按整格算质量会翻倍，且默认无诊断。

**修改**（`src/odbpp/equiv.py`、`src/odbpp/cli.py`、`src/odbpp/diagnostics.py`）：

- CSV 新增 `cell_area_mm2`、`domain_area_mm2`、`domain_coverage` 三列。
  `domain_coverage = 域内像素数 / 单元像素数`，`domain_area_mm2 =
  domain_coverage × cell_area_mm2`。
- 元数据 schema 升为 `odbpp/materialmap-2`，并新增 `coverage` 段，明确
  **下游语义**：`cell_x0…cell_y1` 是共享网格单元边界；物性只适用于
  `domain_coverage` 覆盖的部分；未覆盖部分是板外/空洞，不是 FR4；计算单元
  质量与热容须乘以 `domain_coverage`，不得补足基底材料。
- `EquivalenceOptions.warn_partial_domains` 默认改为 `True`；每层一次
  `equiv.partial_domain` 汇总告警（含最小覆盖率）。CLI 支持 `--no-warn-partial`
  静默，但覆盖率列始终写出。
- 共享网格与 region 边界不变，满足「各物理层共用同一套 XY 网格」。

**验证**（`TestPartialDomain`）：域覆盖半格时 `domain_coverage≈0.5`、
`domain_area_mm2≈半格`、`partial_domain=1` 且有默认告警；全域覆盖时
`domain_coverage=1`。`TestOutput::test_csv_and_metadata_are_written` 校验
meta 的 schema 与 `coverage` 语义。

## 3. [P1] 材料参数统一做有限正数校验

**根因**：`Material.__post_init__()` 只检查 `<= 0`，`NaN` 与 `±Inf` 都能通过；
`_parse_override()` 也不校验有限性。输入 `k1=NaN` 会得到
`kx=ky=kz=NaN`、`fatal=False`、无诊断。0 等值还会在 `resolve()` 时才抛
`ValueError`，逸出 CLI 的 `EquivalenceError` 捕获范围。

**修改**（`src/odbpp/materials.py`、`src/odbpp/cli.py`）：

- `_parse_override()` 对每个字段校验 `math.isfinite(value) and value > 0`；
  否则记 `materials.invalid`，该相退回项目默认值继续运行。
- `Material.__post_init__()` 同样改为有限正数校验，作为第二道防线。
- CLI 等效命令同时捕获 `ValueError`，返回 2 并清理临时文件。

**验证**（`TestMaterials` 参数化 `NaN/Inf/-Inf/0/-1`）：均出现
`materials.invalid`，解析值有限正数；`test_a_non_finite_material_never_reaches_
the_output` 校验全铜图案在 `k1=NaN`、`rho1=Inf` 下每个单元的
`kx/ky/kz/density/cp` 均为有限正数且 `fatal=False`。

## 4. [P1] 矩形化失败即致命，不再写结果

**根因**：`rectify.py` 能检测填平孔、丢失岛与超精度偏差并把
`RectifyResult.ok` 置 `False`，但 `equivalence_from_document()` 未把
`rectification.ok=False` 纳入 `fatal`，CLI 仍写 CSV。复现中一个孔消失，结果
仍含 100 个单元并允许写文件。

**修改**（`src/odbpp/equiv.py`）：主流程初始化
`fatal = drill_fatal or not rectification.ok`；CLI 既有的 `result.fatal` 路径
会删除临时文件、打印 error 级诊断并返回 2。矩形化内部已分别记
`rectify.area_error`/`island_lost`/`hole_filled`，无需新 code。

**验证**（`TestOutput::test_a_rectification_that_fills_a_hole_is_fatal` 与
`TestCommandLine::test_a_rectification_failure_writes_no_csv`）：0.2 mm 窄槽
在 5 mm tolerance 下 `ok=False`、`fatal=True`，等效命令退出码 2，输出目录
无 `*.csv`、无 `*.tmp`。

## 5. [P2] 材料来源按物性字段记录

**根因**：`_Override.apply()` 始终保留默认材料的 `source`，provenance 也只
记一个 `origin`/`source`。只覆盖铜的热导率时，密度与比热沿用默认值却仍报
同一来源，且不触发默认告警。

**修改**（`src/odbpp/materials.py`）：`resolve()` 改为**逐字段**应用
`layer_override > user_global > user_default > project_default`，provenance
新增 `fields.<name> = {value, origin, source}`；`origin` 仍保留为最高优先级
汇总值以兼容既有读取方。任一字段回退到项目默认值时按 (相, 字段集合) 记
`materials.default_used`。

**验证**（`TestMaterials::test_a_global_override_wins_over_the_default`、
`test_provenance_is_recorded_per_field`）：只覆盖 `k` 时，
`k=user_global`、`density/cp=project_default`，并出现字段级默认告警。

## 6. [P2] 修正实现文档的导热公式

**根因**：`docs/equivalence/01-material-equivalence.md` 把 `kx` 写成
`1 / mean_rows(1 / mean_cols_in_row(k))`，既多了一层外倒数、又漏了内层
`1/k`，与代码（行内调和、行间算术平均）及推导文档不一致。

**修改**：改为

```text
kx = mean_rows( 1 / mean_cols_in_row( 1 / k ) )
ky = mean_cols( 1 / mean_rows_in_col( 1 / k ) )
```

并补一句说明：行内对 `1/k` 取列平均后取倒数（串联），行间取算术平均
（并联），切勿写成对行平均再取倒数。推导文档本身公式正确，未改。

## 7. 实现文档引用的本地文件纳入版本控制

**问题**：`AGENTS.md` 有未提交修改，`docs/derivation/` 两份推导文档与
`docs/Flotherm_reference_MaterialMap.csv` 未被 Git 跟踪，而已提交的
`docs/equivalence/01-material-equivalence.md` 直接引用它们，新检出无法完整
复现。

**处理**：随本次修复一并 `git add`：

- `AGENTS.md`（工作树中的需求定稿）；
- `docs/derivation/串并联PCB等效热模型推导.md`、
  `docs/derivation/密度和热容等效热模型推导.md`；
- `docs/Flotherm_reference_MaterialMap.csv`。

参考 CSV 原始大小 122,692,528 字节，SHA-256
`9d9ac2edb37b18e3bc80af8c9d1013fd288e93ef2b278b43177a7e8d131d2ae5`。
其内容为 Flotherm `MaterialMap` 文本网格（1820 行、行长极大、重复 token 多），
zlib 压缩后约 0.98 MB，因此 Git 对象不会显著膨胀；仍应在提交信息中记录该
事实，避免后续误以为仓库很小而直接大改。厂商规格 PDF 仍按既有策略不跟踪
（`.gitignore`），由 `tools/fetch_spec.py` 获取。

---

## 验证方式

```text
.venv/Scripts/python.exe -m pytest -p no:cacheprovider
# 653 passed
```

新增/更新的边界测试集中在 `tests/test_equiv.py`：

- `TestDrillProjection::test_a_drill_spanning_two_planes_does_not_overlay_a_third`
- `TestPartialDomain::{test_a_partial_cell_reports_coverage_instead_of_a_full_cell,
  test_partial_coverage_is_warned_by_default,
  test_a_fully_covered_cell_has_unit_coverage}`
- `TestMaterials::{test_a_non_finite_or_non_positive_override_is_rejected,
  test_a_non_finite_material_never_reaches_the_output,
  test_provenance_is_recorded_per_field}`
- `TestOutput::test_a_rectification_that_fills_a_hole_is_fatal`
- `TestCommandLine::test_a_rectification_failure_writes_no_csv`

## 残留与说明

1. 覆盖率是按**像素中心**统计的离散量，与真实多边形面积可能有亚像素差；
   这与物性统计口径一致，已在 meta 的 `coverage.semantics` 中声明。
2. 跨物理层不做 Z 方向合并、板外不补 FR4 等既有约定不变；本轮只把“部分
   覆盖”从隐含状态提升为显式列与默认诊断。
3. `odbpp/materialmap-1` 布局不再由本版本写出；读取方应据 schema 分支。
