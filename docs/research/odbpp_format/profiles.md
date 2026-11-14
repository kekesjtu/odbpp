# Step profile 与 layer profile

依据：Siemens *ODB++Design Format Specification*, Release 8.1 Update 4，[本地 PDF](../../reference/ODB++_Format_User_Guide.pdf)、[官方 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf)。重点为 p.38–40、p.138–139、p.181–184。

## 1. 两种 profile 分别描述什么

`step_profile` 和 `layer_profile` 是便于讨论的名称。实际文件都叫 `profile`，所属目录决定其含义：

```text
<product_model>/steps/<step>/profile
<product_model>/steps/<step>/layers/<layer>/profile
```

Step profile 定义整个 step 的外形。Step 可以是单板，也可以是拼板、生产面板或测试条；因此不能把 step 一律理解为单块 PCB。Layer profile 定义特定 step 中某一层的物理边界，主要用于描述刚挠结合板各层不同的覆盖范围。（p.44、p.138、p.182）

| 比较项 | Step profile | Layer profile |
| --- | --- | --- |
| 所属实体 | Step | 某 step 中的 layer |
| 几何载体 | 一个 surface feature | 一个 surface feature |
| 岛的数量 | **只能有一个岛** | **可以有多个岛** |
| 孔 | 可以有任意数量的内部孔 | 各岛可以包含孔 |
| 孔的限制 | 孔不能触碰外岛边界，也不能彼此触碰 | 岛必须完整包围其孔，孔之间不能触碰或相交 |
| 缺省关系 | 为逐层轮廓提供缺省外形 | 未定义时，视为等于所属 step 的 profile |
| 文件单位 | 文件自身 `UNITS` | 文件自身 `UNITS` |
| 文件表中的压缩标记 | None | Yes |

“一个 surface”与“一个岛”不能互换：surface 可以承载多个多边形边界。Step profile 的单岛限制来自 p.138 的专门规定；一般 surface 支持多岛，不能据此把 step profile 也扩展为多岛。Layer profile 的多岛规定见 p.181–183。

## 2. Profile 不等于该层的图案

同一层中，下面两个文件回答不同问题：

```text
steps/board/layers/top/profile
steps/board/layers/top/features
```

- `profile`：该层的物理边界在哪里。
- `features`：该层有哪些焊盘、线、弧、面、文字等图形。

例如铜层 profile 可以是整块板的外形，而 features 只有若干彼此分离的走线和焊盘。没有铜图形的地方，不会自动成为 profile 的孔。对负性铜层，features 还可能表达去铜区，材料含义须结合 matrix 中的层极性解释。（p.66、p.164、p.182）

Profile 也不是钻孔表。Profile 内的 cutout 直接参与该轮廓定义；DRILL 层上的钻孔图元和 tools 记录属于另一组制造信息，不能只因几何上都是“孔”就把它们等同。

## 3. 没有 layer profile 时

规范 p.183 明确规定：没有定义 layer profile 时，假定它等于该层所属 step 的 profile。如果所有层的外形相同，无须为每层重复提供 profile。

例如普通矩形板的铜层、介质层具有相同外形，可以共享 step profile 的缺省语义。这个规则只解释**未定义逐层轮廓**的情况，并未授权用任意错误、残缺或不合法的 profile 表示缺省。

Step profile 在 p.22 的全局必需文件清单中没有列出；p.138 说明许多操作需要它。因此“层轮廓可以省略”不等于“缺少所有轮廓时仍能从规范得到唯一外形”。没有明确几何依据时，不能把图元包围盒直接当作规范定义的 profile。

## 4. 定义了 layer profile 时，如何与 step profile 共同作用

### 4.1 Step profile 的孔继续存在

规范 p.182 对孔给出明确规则：

1. Step profile 的孔若位于 layer profile 的岛内，视为该层也存在此孔。
2. Step profile 的孔若与 layer profile 的岛相交，重叠孔区从层岛中扣除。
3. 层文件不必重复声明这些 step profile 孔。

因此，定义 layer profile 并不取消 step profile 的孔。层自身声明的孔与相关的 step 孔共同决定该层剩余区域。

### 4.2 集合形式

下面是对上述规范文字的数学整理，而非新文件语法。设：

- `L`：按 layer profile 的岛、孔及包含关系形成的区域；
- `H_step`：step profile 中所有孔的区域之并；
- `S`：step profile 的完整区域，即其外岛扣除内部孔。

则可以写为：

```text
未定义 layer profile：LayerRegion = S
已定义 layer profile：LayerRegion = L \ H_step
```

第二行保留 layer profile 自身的孔，因为这些孔已经体现在 `L` 中。与 `L` 没有重叠的 step 孔不改变结果。孔相交后可能形成开口、把岛分开，甚至完全移除某个岛，这些都是集合扣除的几何结果。

### 4.3 不要把孔规则扩大成整个外形的相交规则

p.182 明文规定的是 **step profile 的孔** 对 layer profile 的作用，并没有在该段规定“有 layer profile 时，把它与 step profile 的整个实体区域求交”。因此不能把 `L \ H_step` 擅自改写为 `L ∩ S`。

两者在层岛超出 step 外岛时不同。准确的表述是：本节没有给出外岛裁剪或层岛必须包含于 step 外岛的额外规则。这也不意味着任意伸出外形都满足制造要求；实际制造约束需要另外的产品定义。

## 5. 三个几何例子

以下坐标均为毫米，矩形区间表示面积范围。

### 5.1 层岛内部的贯通 cutout

Step 外岛为 `[0,100] × [0,60]`，内部孔为 `[40,60] × [20,40]`。某层单独定义外岛 `[10,90] × [10,50]`，没有声明自己的孔。

最终层区域是该层矩形扣除 step 内孔。层文件没有 `H` 边界，也不能据此判断该层没有孔。

### 5.2 Step 孔与层岛边缘相交

沿用同一个 step 孔，某层外岛变为 `[10,50] × [10,50]`。孔的 `[40,50] × [20,40]` 部分从层岛中扣除，形成从右边缘进入的缺口。

Step 文件里的孔仍必须是 step 外岛内部的合法闭合孔；最终逐层形状呈现缺口，是跨文件孔规则作用后的结果，不是允许 step profile 直接写一个触边的内部孔。

### 5.3 层自身孔与 step 孔有重叠

假设 layer profile 自身定义孔 `H_layer`，且它和 `H_step` 相交。被扣除的面积是两个孔区域的并集，重叠区域只扣一次。不能简单把两个孔的面积相加来计算剩余面积。

## 6. 刚挠结合板中的意义

一块刚挠结合板可以有左侧刚性区、中央柔性连接区和右侧刚性区。Step profile 给出整个板件外形；某个刚性介质层只覆盖左右两侧时，它的 layer profile 可以包含两个分离岛。柔性材料层则可以采用贯穿柔性连接区的另一个轮廓。

```text
整个 step：   [左刚性区]====柔性连接区====[右刚性区]
刚性介质层： [左侧岛]                    [右侧岛]
柔性材料层：       [连续的柔性材料区域]
```

这说明“同一个 matrix 层序”不要求所有层都有相同的 XY 覆盖范围。Matrix 的 `FORM=RIGID|FLEX` 描述层的刚柔性质；profile 描述其平面边界，二者不是同一个信息。（p.66、p.182）

`steps/<step>/zones` 则从区域角度描述具有相同材料与层组合的截面区域。它和 layer profile 分别从“区域含哪些层”与“某层覆盖哪里”表达信息，不能把 zones 文件名当作 layer profile 的别名。（p.142–146）

## 7. 文件表示与方向

Profile 采用 features 的 surface 语法。下面是一个教学用 step profile，外岛顺时针、孔逆时针，单位为毫米：

```text
UNITS=MM
F 1
S P 0
OB 0 0 I
OS 0 60
OS 100 60
OS 100 0
OS 0 0
OE
OB 40 20 H
OS 60 20
OS 60 40
OS 40 40
OS 40 20
OE
SE
```

`I/H` 指明岛与孔；方向和闭合性也必须满足规范。`OS` 是无宽度的直边，`OC` 是无宽度的圆弧边；它们不是带符号线宽的 `L/A` 图元。Layer profile 可在同一个 `S ... SE` 中增加其他互不接触、互不相交的岛，但 step profile 不能照此增加第二个外岛。（p.38–40、p.138、p.178–183）

## 8. 查阅原文

| 页码 | 内容 |
| --- | --- |
| p.38–40 | Surface、岛孔顺序、闭合和方向约束 |
| p.138–139 | Step profile 的文件位置、单岛与内部孔 |
| p.178–180 | S、OB、OS、OC、OE、SE 语法 |
| p.181–184 | Layer profile 的多岛、可选性、step 孔作用及缺省关系 |
| p.142–146 | Zones 的区域和层材料组合 |
