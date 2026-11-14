# `$`、`@`、`&`：声明、查找表与属性赋值

依据：Siemens *ODB++Design Format Specification*, Release 8.1 Update 4，[本地 PDF](../../reference/ODB++_Format_User_Guide.pdf)、[官方 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf)。主要参考 p.27–34、p.84–90、p.164–180。

## 1. 这些前缀建立查找表，不直接画图

在 layer 或 symbol 的 features 文件中，`$`、`@`、`&` 将长名称和字符串映射为局部数字索引，供后续图元记录引用。它们不是 shell 变量或正则表达式符号，也不是图形操作命令。

| 前缀或记录 | 功能 | 示例 |
| --- | --- | --- |
| `$` | 符号索引 → 符号名称及可选单位 | `$0 r200` |
| `@` | 属性索引 → 属性名称 | `@0 .geometry` |
| `&` | 字符串索引 → TEXT 属性可使用的字符串 | `&0 connector_pad` |
| `P/L/A/S/T/B` | 创建具体图元 | `P 10 20 0 P 0 8 0` |
| `#` | 整行注释 | `#Feature symbol names` |

文件中的注释标题只是为了可读性。例如 `#Feature attribute names` 不会创建表；真正声明表项的是 `@...` 记录。即使去掉这些注释，记录自身仍有明确类型。（p.31、p.164–168）

## 2. `$`：符号名称表

常见声明形式：

```text
$<index> <symbol_name>
$<index> <symbol_name> M
$<index> <symbol_name> I
```

`M` 表示公制符号尺寸，`I` 表示英制符号尺寸；表示长度的标准符号参数分别使用 µm 和 mil。未指定时由 features 文件的单位体系决定。（p.166–168）f

```text
UNITS=MM
$0 r200
$1 rect1000x500 M
$2 r10 I
$3 connector_target
```

含义如下：

- 符号 0：直径 200 µm 的标准圆。
- 符号 1：宽 1000 µm、高 500 µm 的标准矩形。
- 符号 2：直径 10 mil，即 0.254 mm 的标准圆。
- 符号 3：自定义符号，图形定义在 `symbols/connector_target/features`。

标准符号由名称参数生成，不要求存在同名目录；自定义符号则依赖产品模型中相应定义。`$3 connector_target` 只是声明引用名称，没有把其完整几何嵌进当前文件。

Pad 通过 `apt_def` 引用表项，line 和 arc 通过 `sym_num` 引用。例如：

```text
P 10 20 1 P 0 8 0
L 0 0 10 0 0 P 0
```

第一个图元引用符号 1，在 `(10 mm,20 mm)` 放置矩形；第二个引用符号 0，用直径 0.2 mm 的圆沿直线拖拽。声明 `$0` 本身不会在原点创建一个圆。

## 3. `@`：属性名称表，不是属性类型定义

```text
@0 .smd
@1 .geometry
@2 .pad_usage
@3 inspection_note
```

这些记录说明图元属性索引分别指向哪个名称。`@` 本身不声明 BOOLEAN、TEXT、FLOAT 或 OPTION 类型，也不为某个图元赋值。属性类型、适用实体、选项列表和单位类别来自系统属性规定及 `misc/sysattr.*`、`misc/userattr` 中的定义。（p.27–29、p.84–90、p.168）

以 `.` 开头的是系统属性；不以 `.` 开头的用户属性应有对应定义。不同属性可适用于不同实体：Layer 属性、Feature 属性和 Component 属性不能只因名称出现在表里就被视为同一作用域。

## 4. `&`：TEXT 属性的字符串表

```text
&0 connector_pad
&1 12345
&2 inspection_target
```

字符串表记录可供 TEXT 属性使用的值。`&1 12345` 的值仍然是文本 `12345`，不会因外观看起来像数字就自动成为整数。

`&` 不会在板上绘制字符；要绘制可见文字，需要 `T` 文本图元。存入 `.geometry` 等 TEXT 属性的是附着在实体上的元数据。（p.168、p.173–176）

符号的含义取决于语法位置：p.28 的 attrlist 示例 `.eda_layers = "VIA CLASS:TOP"&"PIN:TOP"&"ETCH:TOP"` 中也出现 `&`，但那不是 features 文件中的 `&<index> <text>` 声明。不能把每个出现的 `&` 都当作字符串表入口。

## 5. 分号之后如何赋值

以普通图元为例：

```text
<图元主体>;<属性赋值列表>;ID=<uid>
```

这里的中文仅解释结构，不是合法文件内容。主体字段一般用空格分隔；第一个分号引入属性区，属性之间用逗号分隔。UID 是单独的可选信息，不是 `@` 表中的属性项。没有属性而只有 UID 时，可以看到双分号形式 `...;;ID=...`。（p.169–172）

属性赋值的数字含义取决于属性类型：

| 属性类型 | 图元属性区形式 | 数字的解释 |
| --- | --- | --- |
| BOOLEAN | `a` | 仅列出属性索引 `a`，表示 TRUE |
| INTEGER | `a=12` | 直接赋整数 12 |
| FLOAT | `a=0.25` | 直接赋浮点值，物理单位由属性定义和文件单位决定 |
| OPTION | `a=2` | 选项列表中的序号 |
| TEXT | `a=2` | `&2` 的字符串值 |

因此，看到 `1=2` 不能立即判断为“属性 1 的数值是 2”。它可能是文本索引、选项索引或直接数值。必须先查 `@1`，再查该属性类型。（p.169–170、p.178–179）

BOOLEAN 的“索引出现即 TRUE”不等于“所有没出现的布尔属性都有明确的物理 FALSE 值”；未赋值、属性默认和实体实际状态需要分别理解。

## 6. 一个完整的教学片段

为同时说明用户属性定义与图元上的赋值，先假定 `misc/userattr` 包含下面的 TEXT 定义。该定义建立 `inspection_note` 的类型和适用实体，并不为任何图元赋值。（定义语法见 p.85–86、p.90）

```text
UNITS=MM
TEXT {
NAME=inspection_note
ENTITY=FEATURE
MIN_LEN=0
MAX_LEN=1000
GROUP=Custom
DEF=
PROMPT=Inspection Note
}
```

以下 features 片段展示三个表和一个 pad：

```text
UNITS=MM
F 1
$0 r200
@0 .smd
@1 .geometry
@2 .pad_usage
@3 inspection_note
&0 connector_pad
&1 12345
P 10 20 0 P 0 8 0;0,1=0,2=0,3=1
```

逐字段解释最后一行：

| 片段 | 含义 |
| --- | --- |
| 第一个 `P` | 图元类型为 pad |
| `10 20` | 放置坐标，按文件单位为 `(10 mm,20 mm)` |
| `0` | 引用 `$0 r200`，符号为直径 0.2 mm 的圆 |
| 第二个 `P` | 图元极性为 positive |
| `0` | 未指定 Gerber dcode / Excellon tool number |
| `8 0` | 任意角度编码：不镜像，旋转 0° |
| 分号后的 `0` | `@0 .smd` 是布尔属性，列出即 TRUE |
| `1=0` | `.geometry` 是 TEXT，取 `&0`：`connector_pad` |
| `2=0` | `.pad_usage` 是 OPTION，取选项 0：`toeprint` |
| `3=1` | `inspection_note` 是 TEXT，取 `&1`：文本 `12345` |

`.pad_usage` 的选项例子由 p.169 明确列出：0 对应 toeprint，后续为 via、g_fiducial、l_fiducial、tooling_hole、bond_finger。不能把 OPTION 的 0 当作空值。

## 7. 索引与 UID 是两类标识

`$0`、`@0`、`&0` 分属不同查找表，可以同时存在。它们的 0 并不意味着三个表项是同一个对象。

这些表属于所在文件。另一个 layer features 或自定义符号 features 中的 `$0` 可以表示另一种符号；不能跨文件按相同索引合并定义。进入自定义符号文件后，其内部图元引用该文件自己的符号和属性表。

UID 则用于产品模型内的持久唯一引用：

```text
ID=216
...
P 10 20 0 P 0 8 0;;ID=123456
```

第一行的 ID 标识文件实体；图元行上的 ID 标识该图元。规范要求已分配的 UID 不改变，且产品模型内其他实体不能使用同一 ID。局部符号索引、属性索引、dcode、matrix ROW/COL 都不能直接当作 UID。（p.30、p.167）

## 8. 声明位置与其他相似记录

规范将 features 组织为单位、可选 ID、可选 F、符号表、属性表及图元。p.168 明确要求属性查找表位于 layer features 图元段之前。示例也先给符号定义，再使用符号。

| 看起来相似的写法 | 实际含义 |
| --- | --- |
| `F 10` | 可选的图元数量记录；不是第 10 个图元，也不是文本行数 |
| `#Num Features` | 注释，不提供图元数量 |
| `ID=216` | 文件或实体 UID，具体看语法位置 |
| `;ID=123456` | 图元 UID 区，不是 `@` 属性赋值 |
| `$$DATE-MMDDYY` | T 文本字符串中的动态文本变量，不是 `$` 符号表声明 |
| `OB ... I` | Surface 的岛边界起点；这里 I 表示 island，不是英制单位 |
| `$2 r10 I` | 这里 I 是 imperial，与上一行的 I 含义不同 |

相同字符在其他文件中也可能有另一种含义。例如 cadnet/netlist 使用 `$<serial_num> <net_name>` 声明网络名。（p.131–134）因此判断语法时，要先确定文件类型和记录位置，再解释前缀。

## 9. Attrlist 与查找表赋值的区别

Attrlist 面向产品、step、layer、symbol、wheel 等实体，通常直接写名称和值：

```text
UNITS=MM
.copper_weight=35
```

Features 的属性区则面向各图元，通过 `@` 和 `&` 间接寻址。以上 `.copper_weight` 是 Layer 属性，不应机械改写成 Feature 属性并附着于每个 pad。

属性定义、属性名称映射、字符串映射和实体上的实际赋值是四个不同层次。把它们分开，才能正确区分“属性存在何种定义”“本文件用哪个索引表示”“可复用的文本是什么”以及“哪个实体取了什么值”。（p.27–30、p.84–90、p.168–170）
