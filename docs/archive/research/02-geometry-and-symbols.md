# 02 · 几何模型、符号展开与多边形规整

> 依据：`docs/ODB++_Format_User_Guide.pdf`（Release 8.1 Update 4）。
> 本文聚焦 Parser 必须实现的几何语义：图元定义、符号展开、多边形方向与孔洞约定、
> 以及「完全正向前实体多边形集合」的构造路径。

---

## 1. 几何图元总览（p.39-40）

ODB++ 只有 **5 类**基础图元，全部以「符号 + 轨迹/位置」定义：

| 图元 | 记录首字符 | 定义方式 | 生成几何 |
|---|---|---|---|
| Pad | `P` | 位置 + 符号 + 朝向 | 符号形状平移/旋转/镜像 |
| Line | `L` | 起点 + 终点 + 符号 | 符号沿直线**扫掠**（笔画） |
| Arc | `A` | 起点 + 终点 + 圆心 + 顺逆 + 符号 | 符号沿圆弧**扫掠** |
| Surface | `S` | 显式多边形边界 | 直接给定闭合轮廓 |
| Text | `T` | 位置 + 字体 + 尺寸 + 朝向 + 字符串 | 由矢量字体展开成笔画 |
| （Barcode） | `B` | 位置 + UPC39 + 尺寸 | 由条码编码展开成笔画 |

关键语义：

- **Line/Arc 是「扫掠」语义，不是「有宽度的线段」**。
  > 原文 p.38：*"Lines and arcs are created by a symbol and two points—a start point and an end
  > point. Lines and arcs are drawn by dragging the symbol by its center along a line or
  > circular curve from one end to the other."*
- 符号**对称**（`r` 圆或宽高相等的 `s` 方）时，线宽 = 符号直径/边长；
  `r` 产生**圆头**，`s` 产生**方头**（p.38）。
- 用**非对称**符号画线属于不规范用法：
  > *"Using non-symmetric symbols (standard or user-defined) for the drawing of lines is not
  > recommended... If such lines are found in a product model, the application must decide how to
  > handle them. The result should resemble the sweep of the symbol from its start point to its
  > end point."*
- 圆弧**只允许用圆符号**（`r`）绘制（p.38）。
- 当圆弧起点 = 终点时视为 **360° 整圆**；ODB++ **不存在单点弧**（p.40）。
- `r0` 是合法符号，面积为 0，建议按单像素显示（p.37）。

---

## 2. 极性与「正向前实体」要求

### 2.1 记录级极性

每个 `P`/`L`/`A`/`S` 记录都有 polarity 字段（p.169-170, p.178）：

- `P` = Positive：覆盖此前已加入该容器（feature container）的图形。
- `N` = Negative：**擦除**此前已加入该容器的图形。

> 原文 p.39：*"Positive features cover all underlying parts of features previously added to the
> feature container. Negative features erase all underlying parts of features previously added to
> the feature container."*

**这是「顺序敏感」的差集运算**：容器内图元必须**按文件出现顺序**累积求差，
不能先整体并集再整体差集。

### 2.2 层级极性

`matrix/matrix` 的 `POLARITY` 字段（p.66）语义完全不同：

- `SIGNAL` / `POWER_GROUND` / `MIXED` 等铜层 `POSITIVE` → 特征是**铜**。
- 这些层若为 `NEGATIVE` → 特征是**基材（clearance）**，背景才是铜。
- `SOLDER_MASK` / `MASK` 层即使 `POSITIVE`，其特征代表的是**物理阻焊上的开窗（clearance）**。
- `COMPONENT`、`SILK_SCREEN`、`DRILL`、`ROUT`、`DOCUMENT` **应恒为 `POSITIVE`**。

> 原文 p.66：*"One exception is the SOLDER_MASK and MASK layers when polarity is required to be
> POSITIVE and features represent clearances in the physical mask."*

**Parser 必须把「记录级极性」与「层级极性」分开处理**，二者是正交的两个概念。

### 2.3 本项目输出要求（AGENTS 约束 2）

Parser 输出必须是**完全正向（Positive）的实体多边形集合**，因此需要：

```
层内：按记录顺序执行 Δ = Σ(+pos_i) − Σ(neg_i)          （保序布尔差）
层间：铜层本身已为正实体；介质层需按约束 3 从板框/钻孔构造
最终：以偶数-奇数填充规则等价的、互不重叠的正向多边形集合输出
```

---

## 3. 表面（Surface）记录与多边形方向约定

### 3.1 语法（p.178-180）

```
S <polarity> <dcode>;<atr>=<value>,...;ID=<id>
OB <xbs> <ybs> <poly_type>        # poly_type: I=island, H=hole
    OS <x> <y> [cw]               # 线段，终点；起点为上一顶点
    OC <xe> <ye> <xc> <yc> <cw>   # 圆弧，终点 + 圆心
    ...
OE
SE
```

- `OB` 开始一个 polygon，`OS`/`OC` 累积顶点，`OE` 结束 polygon，`SE` 结束 surface。
- 圆弧的 `cw` 必须正确读到，否则圆弧方向反转、多边形自交。
- **最后一个 `OS`/`OC` 的坐标必须与 `OB` 坐标相同**（p.179）。

### 3.2 方向与包含关系（p.38-39）

规范对合法 contour 的约束是硬性的：

> *"The direction of segments representing the edges of an island must be clockwise, and the
> direction of segments representing the edges of holes must be counter-clockwise."*（p.39）

- **island 边界 = 顺时针（clockwise）**
- **hole 边界 = 逆时针（counter-clockwise）**
- island 之间**不得重叠/相接**；hole 不得与 island 边界或其他 hole 相交。
- 多边形必须闭合，**至少 3 条边**，边长 > 0。
- 序列顺序为「自然包含序」：最外层 island 在前；island 先于其内的 hole；hole 先于其内的 island。
  （p.38 示例中顺序为 A, B, C, D, E）

### 3.3 自交禁止（p.40）

> *"In all polygon representations, a self-intersecting polygon (SIP) is illegal."*

即规范**要求输入不自交**。但生产数据并不保证，Parser 需要具备「自交检测 + 修复
（如 `buffer(0)` / union 自身）」的兜底能力，并记录 warning。

### 3.4 圆弧离散化

`OC` 是真正的圆弧段，不能简单地用弦替代（会造成面积与 DRC 误差）。
建议按圆心角离散为多段线，步长按弦高误差控制（例如弦高 ≤ 1 µm 或 弦长 ≤ 0.05 mm，取更严者）。
离散必须保持规范的方向约定。

---

## 4. 标准符号展开

标准符号（Appendix A, p.187-206）的特点是：
**没有独立几何定义文件，必须由名字就地展开**。

> 原文 p.36：*"Standard symbols are rendered dynamically from their names. They do not require
> that a graphic symbol entity be saved."*

### 4.1 命名语法（p.36）

```
<symbol_name_prefix>[<param>[x<param>[...]]]
```

参数用 `x` 分隔，小数分隔符必须是 `.`。
保留前缀不得用于自定义符号（p.37）。

### 4.2 基础符号清单（p.187-200）

**必须原生支持的（AGENTS 约束 5 明确点名 `r`/`s`/`rect`/`oval`）**：

| 前缀 | 形式 | 参数 | 几何 |
|---|---|---|---|
| `r` | `r<d>` | 直径 | 圆 |
| `s` | `s<side>` | 边长 | 正方形 |
| `rect` | `rect<w>x<h>` | 宽、高 | 矩形 |
| `oval` | `oval<w>x<h>` | 宽、高 | 椭圆（长圆/跑道形） |
| `di` | `di<w>x<h>` | 宽、高 | 菱形 |
| `oct` | `oct<w>x<h>x<r>` | 宽、高、角尺寸 | 八边形 |
| `el` | `el<w>x<h>` | 宽、高 | 椭圆 |
| `tri` | `tri<base>x<h>` | 底、高 | 三角形 |
| `hex_l` / `hex_s` | `hex_?(w)x(h)x(r)` | 宽、高、角尺寸 | 横/竖六边形 |
| `bfr` / `bfs` | `bfr<d>` / `bfs<s>` | 直径/边长 | 蝴蝶形 |

**圆角/倒角矩形（Appendix A "Rounded/Chamfered Rectangles", p.203）**：

| 形式 | 语义 |
|---|---|
| `rect<w>x<h>xr<rad>` | 四角全圆角 |
| `rect<w>x<h>xr<rad>x<corners>` | 指定角圆角 |
| `rect<w>x<h>xc<rad>` | 四角全倒角 |
| `rect<w>x<h>xc<rad>x<corners>` | 指定角倒角 |

`<corners>` 是**位掩码**，角按**从右上角开始逆时针升序**编号：

> 原文 p.203：*"The corners of rounded/chamfered rectangles are specified in ascending order
> counter-clockwise, starting from the top-right corner."*
>
> 例：`rect100x50xc8x12` 中 `12` 表示**右上 + 左上**两角倒角。

**环形（donut）**：`donut_r<od>x<id>`（圆环）、`donut_s<od>x<id>`（方环）、
`donut_sr<od>x<id>`、`donut_s<od>x<id>xr<rad>x<corners>`（圆角方环）、
`donut_rc<ow>x<oh>x<lw>`（矩形环）、`donut_o<ow>x<oh>x<lw>`（椭圆环）。
语义均为「外轮廓 − 内轮廓」，天然带孔。

**热焊盘（thermal）**：`thr`/`ths`（圆）、`s_ths`/`s_tho`（方）、`s_thr`（线）、
`sr_ths`（方圆）、`rc_ths`/`rc_tho`（矩形）、`o_ths`（椭圆）、`oblong_ths`（长圆）。
参数含义为 `od`/`os`（外径/外尺寸）、`id`/`is`（内径/内尺寸）、`angle`（缺口起始角，自 0°）、
`num_spokes`（辐条数）、`gap`（辐条缺口宽）、`air_gap`（基材间隔）。
`od`/`id`（或 `os`/`is`）共同控制**基材间隔**。

**钢网设计专用（p.197-202）**：`hplate`、`rhplate`、`fhplate`、`radhplate`、`dshape`、
`cross...`、`dogbone...`、`dpack`。圆角可行性的判据为 `L > Ra/tan(Aa/2) + Rb/tan(Ab/2)`。

**其他**：`moire`（靶标）、`hole<d>x<p>x<tp>x<tm>`（钻孔轮专用）、`ext`（空符号，
用于非图形特征占位）。

### 4.3 角尺寸语义（p.204）

`oct` 的 `r` 是**包围盒角点到顶点的距离**，不是内切半径。这类「参数几何含义随符号变化」
的情况必须在每个符号的展开器中单独实现并写测试。

### 4.4 已废弃符号（p.204-205）

`rc_ths...`（开角圆角矩形热焊盘）与 `o_ths...`（开角椭圆热焊盘）已废弃，
但仍可能出现在历史数据中。建议**支持解析但标记 deprecated**。

---

## 5. 自定义符号展开（AGENTS 约束 5）

> 原文 p.37：*"User-defined symbols have a full graphic description stored in `features` files in
> the product model `symbols` directory. A user-defined symbol can contain any number of features,
> including any number of other user-defined symbols. **Circular references are prohibited.**"*

要点：

1. 定义位于 `<product_model>/symbols/<symbol_name>/features`，语法与层 `features` **完全相同**。
2. 符号可以**嵌套引用**其他自定义符号 → 必须**递归展开**。
3. **禁止循环引用**（规范保证），但工程上仍需**环检测**（记录父链，命中即报错），
   否则畸形数据会导致无限递归。
4. 符号是**可缩放的**（scalable，p.37）：`apt_def = -1 <sym_num> <resize_factor>`
   表示符号被缩放后再放置。resize factor 单位为 mil/µm（见 `01` 第 3.3 节）。
5. 符号内坐标是**相对坐标**，原点 (0,0) 即插入点（p.37 的箭头示例）。
6. **符号内部同样含极性**：符号的 `features` 里可以有 `N` 记录，展开时须在符号局部
   执行保序差集，再把结果整体变换放置。

---

## 6. 变换语义（p.34-35, p.170-171, p.203）

- **旋转恒为顺时针**，角度为 0~360 的正值。
- **镜像只沿 x 轴**（翻转 x 坐标）。
- **旋转与镜像同时存在时：先旋转，后镜像。**

`orient_def` 编码（`P`/`T`/`B` 共用）：

| 值 | 含义 |
|---|---|
| `0`~`3` | 0/90/180/270°，不镜像（legacy，需兼容读取） |
| `4`~`7` | 0/90/180/270°，沿 x 轴镜像（legacy） |
| `8 <angle>` | 任意角旋转，不镜像 |
| `9 <angle>` | 任意角旋转，沿 x 轴镜像 |

注意：`8`/`9` 是**两段式**，后面紧跟角度。解析时不可把 `8 30.0` 当作两个独立字段处理。

`apt_def` 编码（Pad）：

| 值 | 含义 |
|---|---|
| `<sym_num>` | 直接使用该符号 |
| `-1 <sym_num> <resize_factor>` | 缩放后使用 |

---

## 7. 位图化/多边形化的落地路径

将上述语义落到「完全正向实体多边形」的推荐流程：

```
1. 读取 features → 记录序列（保留顺序）
2. 每条记录展开为「正/负多边形集合」：
     P: 符号多边形 → 平移 → 旋转(顺时针) → 镜像(x)
     L: 符号沿线段扫掠。若符号对称：Minkowski 和(线段, 符号)
                          若非对称：按规范建议的「扫掠近似」并记 warning
     A: 符号沿圆弧扫掠；仅允许圆符号 → 环形扇区 + 两端半圆帽
     S: 直接取 OB/OS/OC 边界；island 顺时针、hole 逆时针
3. 层内按记录顺序累积：pos = union(pos, +f) ; pos = difference(pos, −f)
4. 自交检测与修复（buffer(0) 或自并）
5. 方向规范化：输出统一为 island=CCW、hole=CW（或其反，取决于下游约定，但必须一致）
6. 输出为 (外环, [内环...]) 的多边形集合，无重叠、无自交
```

第 2 步中 `L`/`A` 的扫掠在数学上是 **Minkowski 和**。工程实现有两条路线：

- **Minkowski 路线**：对圆/方符号（对称）可解析构造；对任意符号需凸分解后逐段扫掠再并集。
- **栅格化路线**：先以足够细的网格栅格化再轮廓化，牺牲精度换鲁棒性。

鉴于规范精度要求 1/400 µm，**建议走 Minkowski 路线**，仅对非对称符号的异常情况回退。

---

## 8. 对 JSON 中间格式的几何字段建议

```jsonc
{
  "polygons": [                      // 完全正向、互不重叠
    {
      "exterior": [[x, y], ...],     // mm，闭合，方向统一
      "holes": [ [[x, y], ...], ... ],
      "net": "GND",                  // 可选：来自 .net_name 属性
      "layer": "F.CU"
    }
  ]
}
```

- 坐标一律 **mm**，`float64`。
- 圆弧已离散为多段线（**保留原始圆弧信息另存**可选，用于高精度下游）。
- 每个 polygon 记录其来源（哪个 layer、哪些 feature id、正/负）以便追溯与调试图。
