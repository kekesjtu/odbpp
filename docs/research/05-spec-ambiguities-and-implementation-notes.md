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
| 记录级负极性 | `fx_negative_layer` |
| 层级 `POLARITY=NEGATIVE` | `fx_negative_layer` |
| 自定义符号三层嵌套 | `fx_nested_symbol` |
| 符号引用环 | `fx_symbol_cycle` |
| resize factor | `fx_nested_symbol`（语义见 §1，仍是未证实） |
| 层级 `profile` 不可解析 → 硬错误 | `fx_bad_layer_profile` |
| step repeat（拼板）→ 告警并忽略 | `tests/test_job.py::TestNestedStepArrays` |
| 倒角掩码只命中单角 | `tests/test_geometry.py`（正文读法） |

真实样本从未出现的图元/参数：`moire`、`ext`、`el`、`bfr`/`bfs`、`*plate`、
`dshape`、`cross`、`dogbone`、`dpack`、热焊盘（`thr`/`ths`/`*_ths`/`*_tho`）。

---

## 11. `T` / `B` 图元：**明确不支持**（AGENTS 约束 6）

用户在 `AGENTS.md` 中新增约束 6：「不需要支持 T/B 图元。」

理由与后果：

- `T`（文本）需要实现矢量字体文件格式（`fonts/standard`，p.48）与字形展开；
  `B`（条码）需要 UPC39 编码器与条码渲染。二者都**不是几何展开**问题，
  与本项目「几何解析 → 多边形 → 预览」的主线无关。
- 已删除：`features.TextFeature` / `features.BarcodeFeature` 两个数据类、
  `_parse_text` / `_parse_barcode` 两个解析器、`tests/fixtures/fx_text_barcode/`
  以及相关测试。
- **保留**：`features._UNSUPPORTED_RECORDS` 中登记 `T`/`B`，遇到时报
  `features.unsupported_record` 诊断而非 `features.unknown_record`。
  这两个 code 的区别是必要的：「unknown」意味着解析器有 bug，「unsupported」
  意味着这是一个已决策的范围边界。同时该告警让「某层少了丝印文字」**可见**，
  而不是无声丢数据。
- 副作用：含 `T`/`B` 的 `features` 文件会出现 `features.count_mismatch`
  （文件里的 `F <n>` 计数包含未建模的记录）。这是预期行为，测试已固化。
- `units.UnitScope.width_factor_to_mm()` 暂时无生产调用方（`width_factor` 只
  出现在 `T` 记录上），保留并注明原因：它是**单位规则**，未来若恢复文本支持
  应从这里查 12-mil 陷阱，而不是重新踩一遍。

---

## 12. 几何合成阶段（步骤 2–3）：判定与实测

本节记录从「已解析的记录」到「正向实心多边形」这一段中，规范未明确、
或一眼看上去会做错的地方。所有数值均由 `tests/test_clipper.py`、
`tests/test_expand.py` 固化。

### 12.1 `L`/`A` 是 Minkowski 拖拽，不是「粗线」

规范 p.38 原文：*"Lines and arcs are drawn by dragging the symbol by its
center along a line or circular curve from one end to the other."*
符号**只平移、不旋转**。

这条判定可以用一个可验证的数字区分两种读法：边长 1 mm 的**正方形**符号，
沿 5 mm 的 45° 斜线拖拽：

| 读法 | 面积 (mm²) |
|---|---|
| 拖拽（不旋转），本实现 | `1 + 5√2·√2 = 11.0` |
| 旋转孔径（Gerber 式） | `5√2·1 + 1 ≈ 8.07` |
| 菱形孔径 | `5·√2 + 0.5 ≈ 7.57` |

实测 **11.0**，与拖拽读法一致，且包围盒为 `(-0.5,-0.5) … (5.5,5.5)`，
即正方形的**端头保持轴对齐**。菱形/旋转读法下包围盒会不同。

### 12.2 圆形符号必须用 `ClipperOffset`，不能先离散化再 Minkowski

**这是实测发现而非推导得到的**。若先用 sagitta 离散圆再与路径做 Minkowski
和，损失的不是「面积」而是**线宽**：半径 0.1 mm 的圆在默认 sagitta
（0.005 mm）下离散成 10 边形，其竖直跨度只有真实直径的 **95%**。
在 10 mm 长的走线上这是 **5% 的线宽误差**——屏幕上肉眼可见，而面积误差
看起来完全正常。

正确做法是对**开放中心线**做半径偏移（`JT_ROUND` + `ET_OPENROUND`），
带宽恒为 `2r`，误差只落在两端圆帽的离散化上。实测：

| r (mm) | L (mm) | 实测宽度 | 实测面积 | 解析面积 |
|---|---|---|---|---|
| 0.1 | 10 | 0.200000 | 2.029387 | 2.031416 |
| 0.5 | 5 | 1.000000 | 5.774739 | 5.785398 |
| 0.015 | 3 | 0.030000 | 0.090446 | 0.090707 |

正方形符号仍用 `MinkowskiSum`：无离散化，且实测精确（见 12.1）。

### 12.3 「近似路线」（单条 `evenodd` 路径）**已放弃**

AGENTS 约束 2 要求完全正向的实心多边形。若用一条 `<path fill-rule="evenodd">`
承载所有环来走捷径，则**两个正向特征的叠加处会被挖成孔**——这与并集语义相反。
它渲染出来「像一块板」，因此在人工核对时不会被发现，这正是它危险的地方。

实际采用：先做布尔运算得到正向区域，再用 `fill-rule="nonzero"` 渲染
（`tests/test_svg.py::TestFillRule` 断言 `evenodd` 从不出现）。
已在会话中明确**不实现**该近似路线。

### 12.4 负性层（`POLARITY=NEGATIVE`）的合成顺序 —— 实测修正

规范 p.39 规定记录按文件顺序绘制，正向叠加、负向擦除；层极性与记录极性
共同决定语义。

**已知陷阱（本实现曾踩中）**：若先把所有记录累积到独立区域 `built`，
再以 `base − built` 求结果，则负性层上第一条**正向**记录（意为"挖空"）
是从**空区域**里做减法，结果为**空**，于是整个电源层变成空白平面。

正确写法只有一个累加器，以基底面积播种，再按运行依次施加：

```
region = base
for (极性, 运行) in 运行序列:
    若 极性 != 层极性反相: region |= run_region
    否则:                  region -= run_region
```

核实：`fx_negative_layer` 的 GND 层 = 200 mm²（20×10 板框）减去两个
2.2 mm 圆形避让 ≈ **192.38 mm²**，且产出 1 个岛 + 2 个孔。
`tests/test_expand.py::TestPolarity` 断言结果严格落在 `(0, 200)` 之间——
「空白」与「未挖空」两种错误都会落在此区间之外。

### 12.5 基底多边形只给 DIELECTRIC 层

AGENTS 约束 3 的措辞是「**介质层**的基底多边形直接继承板框」。
早期版本把它套用到**所有非铜层**，结果丝印、阻焊、以及全部 `DOCUMENT`
层的面积都变成同一个可疑的 ~20477 mm²（= 板框面积），也就是把整块板涂成
丝印色。现已收敛为：仅 `DIELECTRIC` 层（以及任何负性层）以板框播种。

这不是"少画一点"的问题：板框面积的丝印层在预览里会把真正要核对的内容
完全盖住，而面积数字本身看不出异常。

### 12.6 零宽度图元（`r0`）**真实存在**，默认不可见

`cases/designodb-rigidflex` 的 `ODB_BOARD_OUTLINE` 层用 **235 条 `r0` 线**
绘制板框。数学上零宽度线不围出任何面积，因此默认被丢弃（几何上正确），
并报 `geometry.empty_result`。

`ExpandOptions.minimum_stroke_mm` / CLI `--minimum-stroke MM` 可将其按
指定宽度画成发丝线；**该替换会报 `geometry.zero_width_stroke`**，
因为它是本实现的发明而非数据。

### 12.7 批处理必须与逐条展开等价

`cases/designodb-rigidflex` 有 471,158 条 `L` 记录。逐条布尔运算不可行，
因此有两条批处理规则，且**都被设计为结果与朴素逐条展开完全一致**：

1. 按**有效极性**切成极大运行段；段内并集可交换，故批内顺序不可观测，
   段间严格按文件顺序施加（这是规范唯一赋予含义的顺序）。
2. 段内**共享同一 stroke 定义**的记录合并为一次 Clipper 调用
   （`stroke_paths` / `sweep_paths`）。两条同宽走线各自扫掠，
   并集与添加顺序无关。

实测：520k 记录 / 43 层 / **62 s**（排除 `DOCUMENT`）。
`tests/test_expand.py::TestBatching` 断言批处理与逐条结果一致。

### 12.8 未证实项：`apt_def` 的 resize factor

规范 p.170 正文称之为 "factor"（"thousandths of the units being used
(mils or microns)"），而它自己的例子（p.172）说 `0.02` 是
"resized by 0.02 mil"。两种读法相差 **1000 倍**。

本实现采用例子的读法（尺寸增量），并把 `resize_mm` 作为**总宽度增量**
（每条边界外移一半）。**`cases/` 中没有任何样本使用该字段**，因此该选择
无法用真实数据验证；触发时一律报 `geometry.resize_unverified`。

### 12.9 规范未规定圆角：resize 与偏移一律用圆角

`rect` 的 `r`/`c` 参数之外，规范没有说明 `apt_def` resize 或任何"向外扩张"
操作在角落处应取圆角还是尖角。本实现一律用 `JT_ROUND`，并在
`clipper.offset` 的文档中写明这是通行读法而非规范结论。

### 12.10 极性反转下的**记录极性**语义（未证实）

层极性与记录极性的组合按 **XOR** 处理：负性层上的负性记录意为"材料"。
规范没有直接给出这个组合的语义，**且 `cases/` 中没有样本同时具备
负性层与负性记录**，故仍属未证实，仅由 `TestPolarity` 固化当前读法。
