# 层厚度：铜、介质与其他材料

依据：Siemens *ODB++Design Format Specification*, Release 8.1 Update 4，[本地 PDF](../../reference/ODB++_Format_User_Guide.pdf)、[官方 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf)。重点为 p.24–25、p.62–67、p.215、p.231。文件和局部记录如何确定有效单位，见[单位体系与作用域](units.md)。

## 1. 厚度字段概览

ODB++ 通过层属性描述铜和其他材料的厚度信息。两个主要字段采用不同的物理量与单位规则：

| 字段 | 公制体系 | 英制体系 | 含义 |
| --- | --- | --- | --- |
| `.copper_weight` | µm | oz/ft² | 公制为铜厚，英制为铜的单位面积质量 |
| `.layer_dielectric` | mm | inch | 介质、阻焊材料或锡膏钢网的厚度 |

字段数值必须与所属属性文件的有效单位体系一起阅读。本篇说明其材料含义、换算和叠层关系；单位声明的优先级及作用范围由单位专题说明。

## 2. `.copper_weight`：名称含 weight，公制值却是厚度

### 2.1 字段定义与存放位置

`.copper_weight` 是 `Float` 类型的 Layer 系统属性，显示名称为 **Copper Thickness**。p.215 给出的范围为 `0.0–1000.0`，默认值为 `1.0`。层属性可以在以下 attrlist 中赋值：

```text
steps/<step>/layers/<copper_layer>/attrlist
```

厚度不存放在铜层的 `P/L/A/S` 坐标中。平面图形定义 XY 形状，层属性描述额外的物理信息，两者共同组成产品信息。

### 2.2 公制：数值直接表示微米厚度

p.24 明确说明公制 copper weight 实际上是 copper thickness，单位为 µm。例如：

```text
UNITS=MM
.copper_weight=35
```

含义为 `35 µm = 0.035 mm` 铜厚。不能解释成 35 mm，也不能解释成 35 oz/ft²。

### 2.3 英制：数值表示单位面积的铜质量

```text
UNITS=INCH
.copper_weight=1
```

含义为 `1 oz/ft²`，即每平方英尺铜的质量为一盎司。它不是 1 inch，也不是 1 mil。这里的 weight 是行业沿用名称，对应的物理量为**面密度**，并非一块特定尺寸板件的总铜质量。

相同数字配合不同单位体系会改变物理意义：

| 数字 | `UNITS=MM` | `UNITS=INCH` |
| --- | --- | --- |
| `.copper_weight=1` | 1 µm 厚 | 1 oz/ft² 面密度 |
| `.copper_weight=35` | 35 µm 厚 | 35 oz/ft² 面密度 |

### 2.4 从 oz/ft² 推导厚度

下面是物理推导，不是 ODB++ 规定的固定换算系数。设铜的面密度为 `σ`、体密度为 `ρ`，均匀铜层的厚度为：

```text
t = σ / ρ
```

采用 `1 oz = 28.349523125 g`、`1 ft = 0.3048 m`：

```text
1 oz/ft² = 0.028349523125 / (0.3048²) kg/m²
          ≈ 0.3051517 kg/m²

t_mm = copper_weight × 0.3051517 / ρ_kg_per_m3 × 1000
```

**示例假设**铜体密度取 `8960 kg/m³`，则 `1 oz/ft²` 对应约 `0.03406 mm = 34.06 µm`。常见工程标称“1 oz 铜约 35 µm”属于近似表达，不能据此声称规范强制采用 35 µm/oz 的精确系数。

铜密度、铜箔标称厚度、加工增铜以及成品公差是不同信息。`.copper_weight` 的字段定义没有进一步把它统一限定为“压合前基铜”或“含全部电镀的成品铜厚”；如需区分，应依据额外材料、叠层或制造说明，不能从字段名自行推断。

## 3. `.layer_dielectric`：不只用于介质层

p.231 定义 `.layer_dielectric` 为 `Float` 类型的 Layer 系统属性，显示名称为 **Thickness of Material**，单位类别为 `INCH_MM`。这意味着公制取 mm，英制取 inch。

| 所属层 | 字段的规范含义 |
| --- | --- |
| DIELECTRIC | 介质材料厚度 |
| SOLDER_MASK | 阻焊材料厚度 |
| SOLDER_PASTE | 金属掩模，即钢网厚度 |

属性说明使用了 `SOLDERMASK`、`SOLDER PASTE` 的文字写法；matrix 的层类型枚举写作 `SOLDER_MASK`、`SOLDER_PASTE`。（p.65、p.231）

对锡膏层，该属性不能直接等同于印刷后的锡膏高度，更不能直接等同于回流焊后的焊点高度。规范在这里明确描述的是钢网。

公制介质层示例：

```text
UNITS=MM
.layer_dielectric=0.2
```

表示 0.2 mm。英制介质层示例：

```text
UNITS=INCH
.layer_dielectric=0.008
```

表示 `0.008 inch = 0.2032 mm`。不能将这里的 `0.008` 按 mil 换算。

## 4. 厚度与叠层关系的边界

Matrix 确定层身份、层类型、物理顺序和钻孔跨层关系。介质层的 `CU_TOP/CU_BOTTOM` 引用铜层 UID，用于表明例如 CORE 材料上下的铜层；这些数值不是 Z 坐标，也不是厚度。（p.62–67）

两个厚度属性也不是完整三维坐标：它们本身没有说明 Z 原点、正方向或每层绝对上下表面。只有在层序、实际存在的物理层、厚度及参考面的定义足够完整时，才能进一步推导 Z 位置。

以下结论不能仅靠两个属性得到：

- 把 matrix 的每一行都当成可累加的实体厚度；钻孔层、绘图层等并非同类实体材料层。
- 把铜层的 35 µm 再加到已包含铜箔的另一项整体厚度上而不检查其定义。
- 把一个刚挠结合板的所有 XY 位置都视为相同叠层；逐层 profile 和 zones 可描述不同覆盖区域。
- 从铜厚推导铜覆盖率、孔金属化壁厚或全部成品制造公差。

`matrix/stackup.xml` 是规范支持的另一份可选材料与叠层描述，含设计方和供应方资料。其 XML 字段和单位应按专门 schema 阅读，不能把本文这两个 attrlist 字段的规则直接套用于所有 XML 厚度字段。（p.52–61）

## 5. 缺失值与默认值

所有系统属性在总体规则中都是可选的。（p.27）`.copper_weight` 的默认值描述、`.layer_dielectric` 的零值条目，不等于一个没有提供厚度信息的产品模型已经具有可信的制造厚度。

阅读数据时应区分：明确给出的值、属性定义提供的缺省值、没有说明的物理信息。尤其不能因 `.copper_weight` 默认数字为 `1.0` 就把所有未注明铜厚的层一律宣称为“35 µm 成品铜”。

## 6. 查阅原文

| 页码 | 内容 |
| --- | --- |
| p.24–25 | Copper weight 的公英制物理含义 |
| p.27 | 系统属性的可选性 |
| p.52–61 | 可选材料与叠层 XML |
| p.62–67 | Matrix 层序、类型及铜层引用 |
| p.215 | `.copper_weight` |
| p.231 | `.layer_dielectric` |
