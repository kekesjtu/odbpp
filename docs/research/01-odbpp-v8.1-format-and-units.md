# 01 · ODB++ v8.1 格式与单位制研究

> 依据：`docs/ODB++_Format_User_Guide.pdf`
> —— *ODB++Design Format Specification*, Siemens Industry Software,
> `product.version.8.1.4` / **Release 8.1 Update 4**, 2024-07-16, 515 页。
>
> 本文所有页码均指该 PDF 的页码。原文关键句以引文给出，便于复核。

---

## 1. 版本与权威性

| 项 | 值 |
|---|---|
| 文档标题 | ODB++Design Format Specification |
| 版本 | 8.1 Update 4（`product.version.8.1.4`） |
| 发布 | 2024-07-16（Siemens Industry Software） |
| 页数 | 515 |
| 本地文件 | `docs/ODB++_Format_User_Guide.pdf` |
| 校验 | `md5 = f57b7264488e8670f81c6b94cb3b9fe1` |

该文件与 odbplusplus.com 官方资源页当前提供的 `odb_spec_user.pdf`
（`/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf`）**字节级一致**，
即 `docs/` 中已存在的是最新官方规范，无需另行下载。

**本项目以该 PDF 为唯一权威依据。** 社区二手文档存在实质性错误，不可作为实现依据，
详见 `04-open-source-reference-survey.md` 第 5 节。

---

## 2. 文件系统模型

ODB++ 是**目录树 + 行记录文本文件**的格式，不是单一文件。传输时才打包为 `.tgz` / `.tar.gz` / `.zip`。

### 2.1 产品模型树（p.43-46, p.62）

```
<product_model_name>/
├── matrix/
│   ├── matrix              # 结构化文本，层/step 的矩阵定义，唯一
│   └── stackup.xml         # 叠层（XML）
├── misc/
│   ├── info                # 产品模型基本信息（含 UNITS，必填）
│   ├── attrlist            # 产品模型属性
│   ├── sysattr[.dfm|.fab|.assy|.test|.gen]   # 系统属性定义
│   ├── userattr            # 用户属性定义
│   ├── last_save
│   └── metadata.xml
├── steps/
│   └── <step_name>/
│       ├── stephdr         # step 头（含嵌套 step 阵列 DX/DY/NX/NY）
│       ├── profile         # 板框，单 surface feature
│       ├── attrlist
│       ├── layers/
│       │   └── <layer_name>/
│       │       ├── features    # 图形特征（行记录）
│       │       ├── profile     # 该层自己的轮廓（可选）
│       │       ├── attrlist
│       │       ├── components  # 仅 comp_+_top / comp_+_bot
│       │       ├── tools       # 仅钻孔层
│       │       ├── dimensions  # 标注层
│       │       └── notes
│       ├── eda/data        # EDA 数据（netlist 等）
│       ├── netlists/{cadnet,refnet}/netlist
│       ├── boms/<bom_name>/bom
│       └── impedance.xml
├── symbols/<symbol_name>/features   # 自定义符号的图形定义
├── fonts/standard          # 矢量字体
├── wheels/<wheel_name>/{attrlist,dcodes}
├── input/
└── user/
```

### 2.2 文件的两种文本语法

规范把文件分为两类（p.31-33）：

- **Line Record Text（行记录文本）**：`features`、`profile`、`tools`、`components`、
  `netlist`、`eda/data`、`wheels/dcodes`、字体文件。每行一条记录，首字符（或 token）标识记录类型。
- **Structured Text（结构化文本）**：`matrix/matrix`、`stephdr`、`misc/info`、`misc/attrlist`、
  `sysattr.*`、`userattr`、`attrlist`。`KEY=VALUE` 行，或 `TOKEN { ... }` 块。**顺序无关**。

### 2.3 压缩（p.31-32）

`features`、`profile`、`components`、`netlist`、`eda/data`、`attrlist`、`dcodes` 等文件
**允许被 gzip 压缩**，压缩后文件名为 `features.Z` 之类的形式（规范称 Large File Compression）。
因此 Parser **必须按「是否 gzip 魔数」而非按文件名**来决定解压与否。

### 2.4 缺失文件的容错（p.92-94, p.164）

- `features` 只应出现在支持的层类型上；其他层类型没有 `features` 是合法的。
- `ID`、`F`（特征计数）记录均为**可选**。
- `<layer_name>/profile` 是可选文件。
- `.features.sum` / `.profile.sum` / `.attrlist.sum` 是校验副产物（`SIZE`/`SUM`/`DATE`/`TIME`/
  `VERSION`/`USER`），**不是**规范正文定义的实体，Parser 应忽略或仅作完整性校验。

---

## 3. 单位制（**本项目最易出错处**）

### 3.1 UNITS 指令的存在性与作用域（p.23-26）

规则按优先级如下：

1. **文件内 UNITS 指令** → 决定该文件内所有度量的单位。
2. 文件内**单条记录**的 `I`/`M` 后缀 → 覆盖该条记录的单位（仅 `features`/`profile` 的
   `$` 符号定义行支持）。
3. 文件无 UNITS 指令 → 采用产品模型默认单位。
4. 产品模型 `misc/info` 无 UNITS 指令 → **默认英制（imperial）**。

> 原文 p.23：*"The default units of measurement for the product model are as defined in the UNITS
> directive in the file `misc/info` of the product model. If the default is not defined for the
> product model, the default is imperial."*

v8.0 起，**任何含坐标或度量的文件都必须带 UNITS 指令**：
> 原文 p.34：*"From ODB++ format v8.0, the UNITS directive must be present in every file that
> contains coordinates or measurements."*

一个文件内 `UNITS` **只能出现一次**，且必须位于首个使用单位的行之前（p.23）。

### 3.2 度量数据的单位对照表（p.24-25，规范原表）

| 数据类型 | Metric (`UNITS=MM`) | Imperial (`UNITS=INCH`) |
|---|---|---|
| area | square mm | square inch |
| **copper weight** | **µm（实为铜厚）** | **ounce per square foot** |
| current | amps | amps |
| **distances, sizes, coordinates** | **mm** | **inch** |
| resistance | ohm | ohm |
| temperature | centigrade | fahrenheit |
| voltage | volts | volts |
| weight | gram | ounce |

**copper weight 的单位会随单位制改变物理含义**，这是 AGENTS 注意事项 3 的原文依据：

> 原文 p.24：*"In metric units, copper weight is actually copper thickness. The application
> converts it accordingly into commonly used neutral units (thickness or weight)."*

即：

- `UNITS=INCH` → copper weight 值是 **oz/ft²**（面密度）。
- `UNITS=MM` → copper weight 值是 **µm**（线厚度）。

**二者不是同一物理量的线性换算，不能用一个常数因子互转。** 归一化到 JSON 时必须先判别
输入单位制，再决定该字段输出为「厚度 µm」还是「面密度 oz/ft²」，或者同时保留原始值与语义标签。

### 3.3 符号尺寸参数的单位（**与坐标不同！**）

坐标与距离用 `mm`/`inch`，但**符号名里的尺寸参数**用更细的单位：

> 原文 p.36：*"Units for parameters that represent size are expressed as a thousandth part of an
> inch or as a micron, depending on the current UNITS directive."*

> 原文 p.166：*"All coordinate values will be interpreted as inches or millimeters. Resize factors
> for user-defined symbols are interpreted as mils or microns."*

| 语境 | `UNITS=MM` | `UNITS=INCH` |
|---|---|---|
| 坐标、距离、`features` 中的 x/y | mm | inch |
| **符号名参数**（`r<d>`、`rect<w>x<h>`、`donut_r<od>x<id>` 等） | **µm** | **mil (1/1000 inch)** |
| 符号 **resize factor**（`apt_def = -1 <sym> <resize>`） | µm | mil |
| `tools` 中 `DRILL_SIZE` / `FINISH_SIZE` | µm | mil |
| `text` 的 `xsize`/`ysize` | 层单位（mm/inch） | 层单位（inch） |

**实测印证**（`cases/kicad-kitchen-sink`，`UNITS=MM`）：

```
$0 r300.0                       → 直径 300 µm = 0.30 mm（典型过孔）
TOOLS { NUM=4  DRILL_SIZE=1143.0 } → 1.143 mm（典型成品孔）
```

**实测印证**（`cases/designodb-rigidflex`，`UNITS=INCH`）：

```
$0 r10.827                      → 10.827 mil = 0.275 mm
$1 donut_r78.74x27.559          → 外径 78.74 mil = 2.000 mm / 内径 27.559 mil = 0.700 mm
```

若把 `r10.827` 当作 10.827 inch 解析，结果会放大 1000 倍——这是最典型的实现陷阱。

### 3.4 单条记录的 I/M 覆盖（p.167）

`$<serial_num> <symbol_name> [<I|M>]` 行末的 `I`/`M` **覆盖文件级 UNITS**：

```
UNITS=MM
$0 r120            ← 120 µm
$1 rect20x60 M     ← 20 µm × 60 µm
$2 rect3x5 I       ← 3 mil × 5 mil
$3 punch_target    ← 自定义符号，无单位参数
```

因此符号解析必须以「每条 `$` 行」为粒度解析单位，不能只读文件头一次。

### 3.5 数值表示与精度（p.23, p.34）

- 小数分隔符**必须**是 `.`；**禁止**科学计数法/指数表示。
- 保留精度：1/400 µm（约 1/10160 mil）；最小线宽/弧宽同为 1/400 µm。
- 坐标范围：(-100, -100) ~ (100, 100) inch，即 (-2450, -2450) ~ (2450, 2450) mm。
- 旋转角：0~360 的正值；**旋转方向恒为顺时针**（p.34）。镜像是**沿 x 轴**镜像（即翻转 x 坐标）。

### 3.6 本项目落地规则

统一以公制 mm 输出（AGENTS 约束 4），换算因子：

| 来源 | 换算 |
|---|---|
| 坐标（INCH） | `mm = inch × 25.4` |
| 符号参数 / resize / drill size（INCH） | `mm = mil × 0.0254` |
| 坐标（MM） | 直通 |
| 符号参数 / resize / drill size（MM） | `mm = µm × 0.001` |
| copper weight（INCH） | oz/ft²，**不换算**，输出时附 `unit` 标签 |
| copper weight（MM） | µm 厚度，`mm = µm × 0.001` |

---

## 4. 属性系统

### 4.1 分类（p.27）

- **系统属性**：名称以 `.` 开头，定义于 `misc/sysattr*`（按类别分文件：
  `sysattr`(product)、`sysattr.dfm`、`sysattr.fab`、`sysattr.assy`、`sysattr.test`、`sysattr.gen`）。
- **用户属性**：名称**不得**以 `.` 开头，定义于 `misc/userattr`。

### 4.2 值与类型（p.28-29）

| 类型 | 在记录中的写法 |
|---|---|
| `BOOLEAN` | 只列属性下标即表示 TRUE |
| `INTEGER` / `FLOAT` | 直接写数字 |
| `OPTION` | 写选项序号 |
| `TEXT` | 写**字符串表下标**，非字面量 |
| `REAL` | 直接写数字 |

### 4.3 查找表机制（p.168-169）

行记录文本中的 `@` / `&` 两段构成间接寻址：

```
@0 .smd
@3 .geometry
@4 .pad_usage
&0 9796334
&2 moire
...
P -0.198 1.62 16 P 0 3;3=2,4=0;ID=123456
```

解读：

- `3=2` → 属性下标 3 即 `.geometry`（TEXT 型）→ 值取 `&2` = `moire`。
- `4=0` → 属性下标 4 即 `.pad_usage`（OPTION 型）→ 取选项 0 = `toeprint`。

**结论：属性解析必须实现「属性名表 + 字符串表」两级间接寻址**，且需从 `misc/sysattr*` /
`misc/userattr` 读回类型信息，才能正确解释裸数字。

### 4.4 记录语法（p.169-170）

```
L <xs> <ys> <xe> <ye> <sym_num> <polarity> <dcode>;<atr>=<value>,...;ID=<id>
P <x> <y> <apt_def> <polarity> <dcode> <orient_def>;<atr>=<value>,...;ID=<id>
A <xs> <ys> <xe> <ye> <xc> <yc> <sym_num> <polarity> <dcode> <cw>;...;ID=<id>
```

`<dcode>`、`;...` 属性段、`;ID=<id>` 均为可选，且可同时缺省。
**分号段是可选的，逗号在段内分隔属性赋值** —— 解析时不能按固定列数切分。

---

## 5. 对 Parser 设计的直接约束

1. **单位解析必须在每个文件级、每条 `$` 行级各做一次**，并保留「该符号的实际单位」以便换算。
2. **`misc/info` 缺失 UNITS 时按英制处理**，不能按公制默许。
3. **`matrix` 文件无单位**（p.62 明确 *"The UNITS directive is not relevant for the matrix file"*），
   其中的 `CU_TOP`/`CU_BOTTOM` 是 **UID 引用**而非名字。
4. **层名/step 名大小写**：实测 KiCad 产物中 `matrix/matrix` 用**大写**，而磁盘目录名为**小写**。
   Parser 必须做**大小写不敏感的实体名解析**，否则在 Linux 上必然失败。
5. **压缩探测按魔数**：`features` 等文件可能是 gzip 流。
6. **数值解析禁止 float 累积误差**：规范精度要求 1/400 µm，坐标比较应用容差或整数化
   （如统一到 1e-6 mm 的整数）。

---

## 6. 待确认 / 存疑项

- `misc/metadata.xml`、`matrix/stackup.xml`、`impedance.xml` 在 v8.1 中为可选且结构较大
  （规范正文 + 附录 D/E 超过 200 页）。**本阶段不纳入**，若后续需要叠层厚度/阻抗，
  再按附录 E 的 XSD 单独实现。
- `.sum` 文件中的 `SUM` 算法规范未定义，本阶段不做校验。
- `stephdr` 的嵌套 step 阵列（`DX/DY/NX/NY`）按 AGENTS 约束 1 **显式不支持**，
  遇到时应记录 warning 而非静默忽略。
