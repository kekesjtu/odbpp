# ODB++ 文件树与产品模型

依据：Siemens *ODB++Design Format Specification*, Release 8.1 Update 4，[本地 PDF](../../reference/ODB++_Format_User_Guide.pdf)、[官方 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf)。主要参考 p.21–33、p.43–46、p.52–76、p.92–97、p.138–146、p.147–186。

## 1. ODB++ 是一棵有引用关系的产品目录树

一个 ODB++ product model 是独立、自包含的目录树。它同时表达制造图形、层序、钻孔跨度、元器件、网络、物料和属性，并不是若干互不相关的绘图文件的集合。（p.21–22）

**ODB++ 没有固定的文件总数**：step、层、自定义符号以及可选实体的数量决定目录和文件数量。根目录打成一个归档文件，也不会把内部产品模型变成单个无层次的文本文件。

规范 p.22 明确提到可以使用标准 tar 和压缩工具传输整棵目录树。传输归档、树内单文件压缩与产品模型本身是三个不同层次。

## 2. 产品根目录

以下展开常见的规范目录；不表示每份数据都必须包含全部条目。尖括号表示实体名占位符，`sysattr.*` 表示一组具体属性定义文件。

```text
<product_model>/
├── matrix/
│   ├── matrix
│   └── stackup.xml
├── misc/
│   ├── info
│   ├── attrlist
│   ├── last_save
│   ├── metadata.xml
│   ├── sysattr
│   ├── sysattr.dfm
│   ├── sysattr.fab
│   ├── sysattr.assy
│   ├── sysattr.test
│   ├── sysattr.gen
│   └── userattr
├── steps/
│   ├── <step_a>/
│   └── <step_b>/
├── symbols/
│   └── <symbol_name>/
│       ├── attrlist
│       └── features
├── fonts/
│   └── standard
├── wheels/
│   └── <wheel_name>/
│       ├── attrlist
│       └── dcodes
├── input/
├── ext/
└── user/
```

| 目录或文件 | 作用 |
| --- | --- |
| `matrix/matrix` | 全产品的 step 列表、层列表、层物理顺序及层间关系 |
| `matrix/stackup.xml` | 可选的材料与叠层 XML，包含设计方和供应方信息 |
| `misc/info` | 产品名、ODB++ 版本、来源、保存信息、产品默认单位、最大 UID 等 |
| `misc/attrlist` | 产品模型实体的属性赋值 |
| `misc/sysattr.*` | 各类系统属性的类型、适用实体、选项、默认值和单位类别 |
| `misc/userattr` | 用户自定义属性的定义 |
| `steps/` | 单板、拼板、测试条等 step 实体 |
| `symbols/` | 可复用的自定义符号定义；标准符号通常不需要这里的文件 |
| `fonts/standard` | 标准字体的图形定义 |
| `wheels/` | 光圈轮及其 dcode 定义 |
| `input/`、`ext/`、`user/` | 根树列出的其他目录；用户附加内容应避免与标准目录冲突 |

目录总图见 p.43–46；属性定义见 p.84–90；符号目录见 p.92–93。

在本版规范中，`matrix/matrix` 必需，`stackup.xml` 可选；matrix 相关内容不一致时以 `matrix/matrix` 为主。不能因为存在 stackup XML 就省略 matrix。（p.52）

## 3. Step 子树

一个产品模型包含一个或多个 step。Step 是完整的多层实体，不是某一层图形，也不是处理流程中的“步骤”。（p.44、p.62）

```text
steps/<step>/
├── stephdr
├── attrlist
├── profile
├── zones
├── layers/
│   └── <layer>/
│       ├── attrlist
│       ├── features
│       ├── profile
│       ├── tools
│       ├── components
│       ├── dimensions
│       └── notes
├── eda/
│   ├── data
│   ├── hdi_netlist
│   └── shortf
├── netlists/
│   ├── cadnet/
│   │   └── netlist
│   └── refnet/
│       └── netlist
└── boms/
    └── <bom_name>/
        ├── bom
        └── files
```

| Step 文件或目录 | 表达的信息 |
| --- | --- |
| `stephdr` | Step 公共数据、基准点、原点及 STEP-REPEAT 子 step 放置记录 |
| `attrlist` | Step 实体的属性 |
| `profile` | Step 外形：一个外岛与可选内部孔 |
| `zones` | 板内具有相同材料、层组合的截面区域 |
| `layers/` | 该 step 各层的具体内容 |
| `eda/data` | 网络、封装、引脚及图元与设计网络之间的联系 |
| `eda/shortf` | 有意短接的图元信息 |
| `eda/hdi_netlist` | HDI 网络信息 |
| `netlists/cadnet/netlist` | CAD 网络表 |
| `netlists/refnet/netlist` | 参考网络表 |
| `boms/<bom>/bom` | 某份物料表 |
| `boms/<bom>/files` | 与该物料表有关的源文件信息 |

Step 引用放在 `stephdr` 中，而不是通过把被引用 step 的目录复制到当前 step 下表达。STEP-REPEAT 通过名称、位置、间距、重复数、旋转、镜像及翻面等字段描述实例；`FLIP` 还涉及层序反转，不能与平面 `MIRROR` 混为一谈。（p.139–142）

## 4. Layer 子树

Layer 的身份和顺序定义在产品级 matrix，具体图形等内容位于各 step 的 `layers/<layer>/`。

| Layer 文件 | 作用 | 不应混淆的内容 |
| --- | --- | --- |
| `features` | Pad、line、arc、surface、text、barcode 等图元和局部查找表 | 并非层厚文件 |
| `profile` | 本层物理边界 | 并非铜图案的全部边界 |
| `attrlist` | 层实体属性，包括可选的铜厚、材料厚度属性 | 并非 `@` 图元属性索引表 |
| `tools` | 钻孔工具类型、孔径和公差等 | 并非图元中心坐标表 |
| `components` | 元器件实例、放置及相关信息 | 适用于元器件层，不是每层必有 |
| `dimensions` | 尺寸标注 | 与制造材料图形不是同一类信息 |
| `notes` | 用户注记 | 不等于可制造的文字图元 |

这些文件按层类型和实际信息选择提供，并不要求每个 layer 都包含表内全部文件。p.164 将 features 的用途具体限定为物理板层图形、NC 钻铣层和其他绘图层；元器件信息有专门的 components 文件。

## 5. Matrix：连接 step 列与 layer 行

规范将 matrix 描述为“行是层，列是 step”的产品模型表示。（p.62）

```text
                 board     panel     coupon
top                ·         ·          ·
dielectric_1       ·         ·          ·
bottom             ·         ·          ·
drill_top_bottom   ·         ·          ·
```

图中的点仅表示 layer 与 step 的关系示意，不是 matrix 文件中的实际记录。

- `STEP` 块：`COL`、`NAME`、可选 `ID`，关联 `steps/<name>`。
- `LAYER` 块：`ROW`、`NAME`、`CONTEXT`、`TYPE`、`POLARITY` 及其他关系字段。
- `ROW/COL` 必须为唯一正整数，可以有间隔；文件中块的书写顺序不能替代其明确编号。
- `TYPE` 区分 SIGNAL、POWER_GROUND、MIXED、DIELECTRIC、DRILL、ROUT、COMPONENT 等。
- `START_NAME/END_NAME` 为钻孔等跨层信息提供端点层名；`CU_TOP/CU_BOTTOM` 则为介质层引用相关铜层 UID。

Matrix 不含几何度量，文件表明确注明其 `UNITS` 不相关。层序不是按层目录名称字典序推导，也不能把 `ROW` 的数值当作厚度或 Z 坐标。（p.62–67）

p.64–65 还要求：matrix 中命名的 step 对应 step 实体；命名的 layer 应在产品模型各 step 的 layers 目录中有相应实体，否则产品模型可能无法读取。仅凭目录名猜测有哪些层，会遗漏 matrix 的关系语义。

## 6. 哪些文件是必需的

p.22 明确列出的全局必需路径为：

```text
<product_model>/matrix/matrix
<product_model>/misc/info
<product_model>/fonts/standard
<product_model>/steps/<step>/stephdr
<product_model>/steps/<step>/layers/<layer>/features
```

其中 features 可以采用 `features.Z` 压缩形式。这个清单应与各实体章节一起阅读：具体层类型使用何种内容文件、引用的自定义符号是否存在等，也会构成依赖。不能只凑齐上述几个文件名就断言产品模型完整。

Step profile 没有列在这份全局清单中，但规范说明许多操作依赖它；layer profile 明确可选，缺少时等于 step profile。外形数据是否足以支持某个操作，与全局必需文件清单是不同问题。（p.138、p.182–183）

## 7. 文件依赖比目录位置更重要

典型引用关系如下：

```text
misc/info                  → 产品默认单位、版本等背景
matrix/matrix              → step 名、层名、层序、相关 UID
steps/panel/stephdr         → steps/board 的重复放置
layers/top/features 中 $n  → 标准符号名，或 symbols/<name>/features
features 中 @n / &n        → 同一文件中的属性名和字符串表
元器件 components         → 本 step 的 eda/data 中的相关定义
```

因此，只抽出某个 `features` 文件不一定能独立重建其全部语义：自定义符号、属性类型定义、层极性、网络信息等可能来自其他位置。（p.22、p.27–30、p.37、p.149–153）

## 8. 文本、压缩与扩展内容

规范的基本文本规则为 ASCII、`LF` 或 `CRLF` 换行，`#` 开头的行是注释。普通字段采用定点十进制，小数点为 `.`；坐标不使用科学计数法。（p.31、p.34）

正文既有 `NAME=value` 和 `{ ... }` 块形式的 Structured Text，也有按记录前缀识别的 Line Record Text。规范还专门定义了 stackup、metadata 等 XML 文件，不能把“目录内所有文件”都当作 features 的行语法。（p.32–33、p.52、p.78）

p.31 说明部分大文件可以使用标准 UNIX 压缩并以 `.Z` 形式存在；哪些实体允许压缩，由各文件章节的 Compression 项决定。例如 features 和 layer profile 标为 Yes，matrix 和 step profile 标为 None。不要把压缩许可无条件扩大到所有文件，也不要把规范的 `.Z` 等同于某个软件另行采用的 gzip/zip 约定。

第三方可以附加额外内容，包括不同编码、二进制或加密数据，但这些内容在规范定义之外；附加目录不应与标准目录冲突。（p.31、p.43）

## 9. 实体命名

p.22 规定 product model、step、layer、symbol、attribute 等实体的合法名称：最长 64 字符，只使用小写字母、数字以及 `- _ . +`；通常不能以 `. - +` 起始，不能以 `.` 结尾。系统属性以 `.` 开头是例外；用户属性不能以 `.` 起始。

规范个别教学示例出现大写实体名，但这不能取代“Legal Entity Names”章节的明确规则。字段关键字、枚举值和实体名应分开理解：`TYPE=SIGNAL` 中的 `SIGNAL` 是枚举，不是层目录必须采用的名称。

继续阅读：[声明与属性](declarations-and-attributes.md)、[图元与符号](geometry-and-symbols.md)、[Profile 关系](profiles.md)、[单位体系与作用域](units.md)、[层厚度](layer-thickness.md)。
