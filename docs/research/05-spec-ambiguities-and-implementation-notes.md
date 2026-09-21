# 05 — 规范歧义与实现期未决问题

本文件记录**在实现过程中实际遇到、但规范原文无法唯一确定**的语义点，以及本
parser 当前采取的读法与验证状态。目的是：任何一条结论被后续数据推翻时，都能
立即定位到「哪一行代码按哪个假设写的」。

页码一律引用 `docs/ODB++_Format_User_Guide.pdf`（Release 8.1 Update 4），可用
`python tools/extract_spec_text.py --page <n> <n>` 复核。

状态标记：

| 标记 | 含义 |
|---|---|
| **已证实** | 有 `cases/` 真实样本或规范自洽的原文/示例双重支撑 |
| **未证实** | 规范只有一处、或只有文字描述，`cases/` 四例均未触发 |
| **自相矛盾** | 规范正文与它自己的示例冲突，必须择一 |
| **已修订** | 与 `AGENTS.md` 初始约束不同，已按实测修订（见 §5） |

---

## 1. resize factor 的物理量（**未证实**）

**规范原文**

- p.166：user-defined symbol 的 resize factor「expressed as a thousandth part of
  an inch or as a micron」——即 `mil` / `µm` 量纲，与坐标不同。
- p.170：`apt_def` 的形式为 `<sym_num>` 或 `-1 <sym_num> <resize_factor>`。
- p.172 示例自述：`P 1.0 2.0 -1 0 0.02 P 4 1` 是「resized by **0.02 mil**」
  （`apt_def = -1 0 0.02`）。

**冲突点**：若 `0.02` 是「千分之一英寸」，则 = 0.02 mil = 0.508 µm；若按示例
文字「0.02 mil」直接读，则完全相同。两句其实一致，但都只说明**单位**，没有
说明**语义**——到底是

- (a) 尺寸增量（delta）：新尺寸 = 原尺寸 + 2×resize；
- (b) 尺寸缩放比例；
- (c) 外扩/内缩（offset/dilation）量。

**当前实现**：`features.PadFeature.resize_mm` 按 (a)「以 µm/mil 为单位的尺寸
增量」读，并原样保留 `resize_raw` / `resize_unit`。**四例 `cases/` 均未出现该
字段**（`census_cases.py` 未检出），因此该读法没有任何实测支撑。

**推翻条件**：出现一个带 resize 的真实样本，且其 pad 尺寸与符号名义尺寸之差
可被量测。

---

## 2. rect 圆角/倒角掩码与规范示例冲突（**自相矛盾**）

**p.203 正文**：角点「specified in ascending order counter-clockwise, starting
from the top-right corner」，即每一位数字代表**一个**角。

**p.203 同一页的示例**：`rect100x50xc8x12` 被描述为「a rectangle with only the
top right corner is chamfered」，同时又说 `12` 表示「top right and top left
corners」。即示例文字声称「只倒一个角」，示例数据却是两个角。

**当前实现**：`geometry._rect` 按**正文**读——掩码中每一位命名一个角，按
p.203 从右上角起逆时针编号 1=右上、2=左上、3=左下、4=右下（`_CORNER_SITES`）。
掩码中出现 1..4 之外的数字时报 `UnsupportedFeatureError`，而不是静默忽略。

**风险**：若真实数据按示例文字理解为「只取第一位」，会把只倒一个角的矩形画成
倒两个角。四例 `cases/` 中的圆角矩形只有 `rect<w>x<h>xr<rad>`（省略掩码，即
四角全倒），**未触发**。

---

## 3. `hex_l` / `hex_s` / `oct` 缺省参数的几何（**未证实**）

规范只给出参数表，未给图形。`oct<w>x<h>x<r>` 中 `r` 是「bounding box corner
到顶点的距离」（p.204）。当 `r` 省略时（Appendix A 的语法把 `x<r>` 写成必填，
但真实导出工具会省略）没有定义。

**当前实现**

- `oct` 省略 `r`：取**内接于 w×h 框、且四边相切**的正八边形，角切量
  `= (2-√2)·(w/2, h/2)`。1×1 框下面积 0.82843，等于边长 `1/(1+√2)` 的正八边形
  解析值，与 `2(1+√2)a²` 相符。
- `hex_l` / `hex_s`：按**跨越整个 w×h 包围盒**构造六边形（水平六边形顶点在左
  右、上下为平边；垂直六边形反之）。若改成「内接于椭圆」，平边会缩到
  `h·√3/2` 而**够不到自己的包围盒**，因此不采用。

---

## 4. `oval` 的长轴方向与 `oval_h` 的剖切位置（**未证实**）

p.188 只写 `oval<w>x<h>`，`w` = Oval width、`h` = Oval height，未说明直边沿长轴
还是短轴；p.192 的 `oval_h<w>x<h>`（Half Oval）也未说明沿哪条轴剖切。

**当前实现**：直边沿**较长**轴（`w ≥ h` 时左右两个半圆帽，反之上下两个），
`oval_h` 取 y ≥ 0 的上半部分并以直径闭合。四例中 `oval*` 未出现（`census_cases.py`
检出 `oval` 前缀 0 次），仅合成 fixture 覆盖。

---

## 5. 介质层基底：`AGENTS.md` 约束 3 的修订（**已修订**）

**原始约束 3**：「介质层的基底多边形直接继承板框文件 `steps/<step>/profile`」。

**实测**：

- `cases/kicad-*`（KiCad 导出）：层级 `features` 为 `F 0`，层级无 `profile`，
  回退到 step profile 成立。
- `cases/designodb-rigidflex.tgz`（Xpedition/Valor 导出）：**23 个层带有自己的
  `profile`**，且包围盒与 step profile 不同。机械套用 step profile 会造出并不
  存在的介质区域。

**现约束 3**（已写入 `AGENTS.md`）：

1. `steps/<step>/layers/<layer>/profile` 存在且可解析 → 用它；
2. 层级 `profile` 缺失 → 回退到 `steps/<step>/profile`；
3. 层级 `profile` **存在但不可解析** → 报错，**不得静默回退**。

**实现**：`model.Step.base_geometry()` 是唯一入口；第 3 种情况抛
`LayerProfileError`，并在 `job._load_layer` 阶段就写入 `layer_profile.invalid`
错误诊断（CLI 因此返回非零退出码）。回归测试见
`tests/test_job.py::TestLayerProfileConstraint` 与 fixture `fx_bad_layer_profile`。

---

## 6. `misc/info` 缺 `UNITS` 时的回退（**已证实**）

p.23：「If the default is not defined for the product model, the default is
imperial.」`matrix/matrix` 本身**不带单位**。

**实现**：`units.DEFAULT_UNIT_SYSTEM = INCH`；`metadata.parse_product_info` 在
缺失时发出 `info.missing_units` 警告（警告而非错误，因为规范允许），其余文件
继承该作用域。

**注意**：该警告必须保留。默认到英制会让「全部坐标 ×25.4」这一类错误**看起来
完全正常**，只有诊断能暴露它。

---

## 7. 特征属性后缀允许出现「裸索引」（**已证实**）

p.178：BOOLEAN 属性「No value is necessary. If the index of the attribute is
listed, the attribute is set to TRUE.」

实测 `cases/kicad-kitchen-sink` 与 `cases/kicad-video-odb.tgz` 的
`F.PASTE` / `F.MASK` / `F.CU` 等层大量出现 `;0=0,1` 形式：属性 0 = 值 `0`，
属性 1 只列索引（布尔 TRUE）。

**实现**：`features._split_record` 把裸索引记为 `None`，`_coerce` 将 `None`
解释为 `True`。早期版本按「每项都必须含 `=`」解析，会直接抛
`ParseError` 而整层无法读取——这是一个只有真实样本才能发现的缺陷。

---

## 8. `@` / `&` 表必须在记录解析期间**实时**可见（**已证实**）

p.168 的两段间接寻址本身无歧义，但实现顺序有陷阱：若先把整个文件扫完再
materialize `@`/`&` 表，则每条记录解析时看到的表都是空的，会把所有属性报成
「未声明」并退回原始 token。

**实现**：`parse_feature_file` 在每次读到 `@`/`&` 行时立即刷新
`result.attr_names` / `result.attr_strings`。回归测试见
`tests/test_features.py::TestAttributeIndirection::test_the_tables_are_live_while_records_are_parsed`。

---

## 9. 本节新增的两类实测反例（**已证实**）

| 现象 | 样本 | 后果（若不处理） |
|---|---|---|
| 矩阵 `LAYER` 块缺 `ROW`，只有 `ROW_NAME` | `cases/minimal-test-odb/matrix/matrix` | 所有层 `ROW=0`，误报 `matrix.duplicate_layer`，且层序退化为目录序 |
| 矩阵 `LAYER` 块 `CONTEXT=MISC` 的层（`EDGE.CUTS`、`MARGIN`、`USER.*`）在 `layers/` 下有真实目录 | `cases/kicad-*` | 若只加载 `CONTEXT=BOARD`，板框 `EDGE.CUTS` 直接消失 |

**实现**：`ROW` 缺失时按文件顺序编号；**全部** matrix 层（含 `MISC`）都加载，
由消费者按 `LayerRef.context` 过滤。

---

## 10. 尚未被任何样本触发的实现分支

以下分支已在 `tests/fixtures/` 中用合成样本覆盖，但**没有真实数据验证**，
一旦拿到真实样本应优先复核：

| 分支 | 合成覆盖 |
|---|---|
| `T` 文本记录、`B` 条码记录 | `fx_text_barcode` |
| 记录级负极性 | `fx_negative_layer` |
| 层级 `POLARITY=NEGATIVE` | `fx_negative_layer` |
| 自定义符号三层嵌套 | `fx_nested_symbol` |
| 符号引用环 | `fx_symbol_cycle` |
| resize factor | `fx_nested_symbol`（语义见 §1，仍是未证实） |
| 层级 `profile` 不可解析 → 硬错误 | `fx_bad_layer_profile` |
| step repeat（拼板）→ 告警并忽略 | `tests/test_job.py::TestNestedStepArrays` |
| 倒角掩码只命中单角 | `tests/test_geometry.py`（正文读法） |

真实样本从未出现的图元/参数：`T`、`B`、`moire`、`ext`、`el`、`bfr`/`bfs`、
`*plate`、`dshape`、`cross`、`dogbone`、`dpack`、热焊盘（`thr`/`ths`/`*_ths`/
`*_tho`）、`width_factor` 非 1 的文本、`;ID=` 之外的自定义属性形态。
