# ODB++ 单位体系与作用域

依据：Siemens *ODB++Design Format Specification*, Release 8.1 Update 4，[本地 PDF](../../reference/ODB++_Format_User_Guide.pdf)、[官方 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf)。重点为 p.23–26、p.76、p.88–90、p.166–172、p.185。

## 1. 单位体系与字段尺度

ODB++ 的 `UNITS=MM` 不意味着文件中所有数值都是毫米；`UNITS=INCH` 也不意味着所有数值都是英寸。声明选定公制或英制体系，具体字段再决定物理量和尺度。

| 数据或字段 | 公制体系 | 英制体系 | 含义 |
| --- | --- | --- | --- |
| 坐标、一般距离和尺寸 | mm | inch | 一般长度 |
| 面积 | mm² | inch² | 平面面积 |
| 标准符号中表示尺寸的参数 | µm | mil | 平面图形尺寸 |
| Pad 的 `resize_factor` | µm | mil | 有长度单位的放大、缩小量 |
| 钻孔工具 `FINISH_SIZE`、`DRILL_SIZE`、`MIN_TOL`、`MAX_TOL` | µm | mil | 孔径及公差 |

依据分别见 p.24–25、p.36、p.166–172、p.185。符号中的角度、数量、比例等参数不因 `MM/INCH` 而成为长度。材料属性也可能采用专用物理量，铜与介质的厚度字段详见[层厚度](layer-thickness.md)。

## 2. 单位作用域与覆盖关系

### 2.1 产品默认、文件声明、局部声明

规范 p.23–26、p.76、p.166–167 描述了三个层次：

1. `misc/info` 中的 `UNITS` 提供产品模型默认单位；没有定义产品默认时，默认为英制。
2. 具有度量数据的文件使用自身 `UNITS`。ODB++ v8 的规则要求这类文件声明单位，且声明在第一次使用度量值之前出现，一个文件只能有一条文件级声明。
3. 某些记录可以局部指定单位，例如 `$` 符号声明末尾的 `M/I`。其作用限于该记录规定的对象。

p.166 同时给出 features 缺少 `UNITS` 时采用产品默认、产品默认也缺少时采用英制的解释规则。应区分“规范要求新文件具备声明”和“规范描述缺失时如何解释”，不能把后者写成所有 v8 文件都可以任意省略单位。

### 2.2 同一个层的文件可以有不同单位

例如：

```text
# misc/info
UNITS=MM

# steps/board/layers/top/features
UNITS=MM

# steps/board/layers/top/attrlist
UNITS=INCH
```

这三段分别表示三个文件的内容。Features 中的坐标采用毫米；attrlist 中的度量属性采用英制体系，再按属性定义确定是 inch、mil 或其他专用单位。产品默认不会覆盖文件的显式声明，兄弟文件也不会继承彼此的单位。

### 2.3 符号的局部单位不改变坐标

```text
UNITS=MM
$0 r200
$1 r10 I
P 10 20 0 P 0 8 0
P 30 20 1 P 0 8 0
```

第一个 pad 的中心是 `(10 mm,20 mm)`，直径为 `200 µm = 0.2 mm`；第二个中心是 `(30 mm,20 mm)`，直径为 `10 mil = 0.254 mm`。`I` 覆盖的是该符号的尺寸体系，不把第二个 pad 的坐标改成英寸。

自定义符号内部 features 也有自己的 `UNITS`。符号内部几何坐标、引用位置和符号名称中的尺寸参数须各按适用规则理解，不能把引用文件的单位无条件套到全部文件。（p.93、p.166–167）

### 2.4 属性定义中的 `UNITS` 是另一层含义

`misc/sysattr.*` 和 `misc/userattr` 可以通过 FLOAT 定义声明物理类别：

```text
UNIT_TYPE=DISTANCE
UNITS=INCH_MM
```

或者采用 `MIL_MICRON` 尺度。规范 p.89 的表格还使用了 `MIL_MICRONS` 拼写，示例使用 `MIL_MICRON`；这些原文写法均指向 mil/µm 这一尺度组合。属性块内的 `UNITS=INCH_MM` 是单位类别，不是文件头部的 `UNITS=MM` 或 `UNITS=INCH`。

`UNIT_TYPE=COPPER_WEIGHT` 则按 copper weight 的专门单位规则解释。不能将所有 FLOAT 属性统一视为一般长度；电压、电流、面积、温度等都有自身单位类别。（p.88–90）

## 3. 长度换算与单位变更

常用长度换算：

```text
1 inch = 25.4 mm
1 mil  = 0.001 inch = 0.0254 mm
1 µm   = 0.001 mm
```

把一个 ODB++ 文件从英制改为公制，必须转换受单位影响的数值。只修改 `UNITS` 行会改变其物理含义；铜重等专用物理量的转换也不能统一乘以 25.4，具体推导见[层厚度](layer-thickness.md)。

## 4. 查阅原文

| 页码 | 内容 |
| --- | --- |
| p.23–26 | 单位声明、默认、覆盖及数据类型单位 |
| p.76 | 产品默认单位 |
| p.88–90 | FLOAT、UNIT_TYPE 与属性单位类别 |
| p.93 | 自定义符号 features 的文件单位 |
| p.166–172 | Features 单位、符号单位、pad resize |
| p.185 | 钻孔工具尺寸及公差单位 |
