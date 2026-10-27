# 04 · 开源参考项目调研

> 目的：为自研 Parser 挑选可借鉴的**结构**（而非代码），并明确许可证边界。
> 调研时间：本仓库初始化阶段。`external/OdbDesign` 为已落地的本地副本，其余为按需拉取。

---

## 1. 参考项目总表

| 项目 | 语言 | 许可证 | ★ | 定位 | 对本项目的价值 |
|---|---|---|---|---|---|
| [nam20485/OdbDesign](https://github.com/nam20485/OdbDesign) | C++20 | **AGPL-3.0** | 83 | ODB++ 解析库 + REST/gRPC 服务 | ★★★★★ **数据结构与文件模型的最佳参照** |
| [KiCad](https://gitlab.com/kicad/code/kicad) `pcbnew/pcb_io/odbpp` | C++ | **GPL-3.0** | — | EDA 套件，含 v8.1 **导出器** | ★★★★★ **规范一致性「标准答案」/ 测试数据来源** |
| [FixturFab/ODB2kicad](https://github.com/FixturFab/ODB2kicad) | Python + Rust/WASM | GPL-3.0 | 5 | ODB++ → KiCad 转换 | ★★★ 样本来源；几何→KiCad 图元的映射思路 |
| [ulikoehler/ODBPy](https://github.com/ulikoehler/ODBPy) | Python | **Apache-2.0** | 42 | ODB++ 轻量解析 | ★★★ **许可证最友好**，可参考 BOM/组件解析 |
| [vlovo/pcb2svg](https://github.com/vlovo/pcb2svg) | C++ | **BSD-3-Clause** | 19 | features/profile → SVG | ★★★ 与本项目 SVG 预览目标最接近 |
| [mcix/odbpp](https://github.com/mcix/odbpp) | Java | 未声明 | 3 | 解析 + SVG/PNG + Gerber 转换 | ★★ 样本来源；架构参考（许可证不明，**只读不抄**） |
| [sjgallagher2/ODBplusplus-Parser](https://github.com/sjgallagher2/ODBplusplus-Parser) | Python | GPL-3.0 | 3 | v7 → 仿真几何 | ★★ 早期实现，v7 语义，注意版本差异 |
| [capablemonkey/odb-pp-parser](https://github.com/capablemonkey/odb-pp-parser) | Ruby | 未声明 | 18 | features/netlist → JSON | ★ 仅作 JSON 结构灵感 |

---

## 2. `external/OdbDesign`（已落地）深度分析

### 2.1 定位与结构

C++20 跨平台 ODB++ 解析库，对外暴露 REST/gRPC。依赖：`libarchive`、`zlib`、`crow`、
`protobuf`、`grpc`（见 `vcpkg.json`）。

```
OdbDesignLib/
├── FileModel/          # 逐文件的行记录解析
│   └── Design/
│       ├── FeaturesFile.{h,cpp}     # features 文件
│       ├── ContourPolygon.{h,cpp}   # S/OB/OS/OC 多边形
│       ├── SymbolName.{h,cpp}       # 符号名解析
│       └── SymbolsDirectory.{h,cpp} # symbols/ 目录
├── ProductModel/       # 语义模型：Design / Step / Layer / Net / Component / Via / Pin
└── protoc/             # 27 个 .proto，定义 IPC 与内部模型
```

`OdbDesignLib/protoc/` 的 27 个 `.proto` 是一份**现成的领域模型清单**，值得逐个对照：
`featuresfile` / `symbolname` / `symbolsdirectory` / `matrixfile` / `layerdirectory` /
`stepdirectory` / `stephdrfile` / `toolsfile` / `miscinfofile` / `attrlistfile` /
`componentsfile` / `netlistfile` / `edadatafile` / `standardfontsfile` / `filearchive` …

### 2.2 可借鉴的点（**结构层面**）

1. **`ContourPolygon` 的建模方式**：把 `OB` 起点 + `Type{Island,Hole}` +
   `vector<PolygonPart{type∈{Segment,Arc}, endX/Y, [xCenter,yCenter,isClockwise]}>` 分开建模。
   → 与我们 `02` §3 的设计一致：**保留原始圆弧语义**，离散化是后续步骤，不丢信息。
2. **`SymbolName` 与 `SymbolsDirectory` 分离**：符号名解析（标准符号文法）与符号定义
   （自定义符号目录）是两个独立模块。
   → 印证 `03` §4.2(a)：标准/自定义消歧需要独立的文法器。
3. **`libarchive` 统一处理 tar/zip/gzip**。
   → 对应我们 `01` §2.3 的「按魔数探测压缩」。Python 侧可用 `tarfile`/`zipfile`/`gzip`
   组合；若需要统一抽象，`libarchive` 的 Python 绑定（`libarchive-c`）是候选。
4. **`OdbDesignLib/protoc/filearchive.proto`** 展示了归档条目 → 逻辑路径的映射设计。

### 2.3 **不可借鉴**：AGPL-3.0 的许可证约束

> OdbDesign 采用 **GNU Affero General Public License v3.0**。

AGPL-3.0 是最强 copyleft 之一，**且对网络服务同样触发开源义务**。因此：

- ✅ **允许**：阅读其文档、数据结构设计、算法思路；在其源码中检索规范细节。
- ❌ **禁止**：复制/改写其代码、proto 定义、注释文本进本项目，除非本项目整体采用 AGPL-3.0。
- ⚠️ 即使是「参考后重写」，若结构与方法签名高度一致，仍存在衍生作品争议。
  → **只借鉴概念，独立实现**；必要时在代码注释中记录「思路来源」，不复制代码。

本地副本 `external/OdbDesign` 仅作为**只读参考**存在（已在 `.gitignore` 中排除，
见仓库根目录说明），不参与构建，不再分发。

### 2.4 OdbDesign **不具备**的能力（正是我们需要自研的部分）

1. **没有多边形布尔运算**：全仓库检索 `clipper` / `boost::geometry` / `GEOS` 均无命中。
   它只做解析与建模，不做正/负极性求差、不做多边形的并/交/差。
   → **`02` §7 的规整流水线必须自研**（用 `pyclipper` / `shapely`）。
2. **没有介质层/钻孔 Z 区间投影**：AGENTS 约束 3 的几何构造在其 ProductModel 中无对应实现。
3. **没有单位归一化到 mm**：它保留原始单位。

---

## 3. KiCad 的价值：规范一致性的「标准答案」

KiCad 自 7.0 起实现了 ODB++ **导出**（依据 *ODB++Design Format Specification Release v8.1
Update 3*，MR !1991），并持续补充 IPC-4761 相关层（MR !2059 / !2180）。

对本项目的三点价值：

1. **它是规范一致性基准**。`cases/kicad-*` 就是 KiCad 直接产物，
   可以用「KiCad 导出 → 我们解析 → 与 KiCad 的 PCB 模型比对」做**双向校验**。
2. **它解释了实测中的「怪现象」**。例如 `cases/kicad-kitchen-sink` 中
   `capping_f.cu-b.cu` / `filling_f.cu-b.cu` / `plugging_front` / `tenting_*` 这些
   **不在 matrix 中的孤层**，正是 IPC-4761 过孔保护层（见 `03` §2.2(d)）。
3. **它的测试代码告诉我们「什么该被验证」**：KiCad 的 QA 用例覆盖
   `test_odbpp_netlist.cpp`（netlist 坐标与 features 一致性）、
   `test_odb_drill_tools.cpp`（钻孔刀具）、`test_odb_dimension_export.cpp`（尺寸标注）。

许可证：KPL-3.0（GPL-3.0 with KiCad exception）。**同样只读参考，不复制代码。**

---

## 4. 其他项目的具体技术点

### 4.1 `ulikoehler/ODBPy`（Apache-2.0，许可证最友好）

- 定位轻量：组件/BOM 提取，不做几何。
- README 声明 *"Tested with DIPTrace 3.x and ODB++ 8.1"* → 可交叉验证 v8.1 解析。
- **可安全借鉴**（Apache-2.0，需保留 NOTICE）：属性表遍历、组件记录解析的写法。

### 4.2 `vlovo/pcb2svg`（BSD-3-Clause，目标最接近本项目 SVG 分支）

- 输入是**已解包的 step/layer 目录**而非归档，读 `features` + `profile` 直接输出 SVG。
- README 自述的限制值得引以为戒：*"currently a small subset of symbols is supported"*。
  → 本项目必须把**符号覆盖率**作为显式指标，不能落进同一个坑（见 `03` §5 缺口 8）。
- **可安全借鉴**：SVG 分层渲染、图层切换的组织方式。

### 4.3 `FixturFab/ODB2kicad`

- 提供 Rust/WASM 实现与浏览器端转换。
- **样本来源**：`samples/odb-kitchen-sink`、`samples/designodb_rigidflex.tgz` 均来自此处。
- GPL-3.0，代码不可复制；但其 `samples/` 目录中的**数据**已按 `cases/README.md`
  的来源表登记。

### 4.4 `mcix/odbpp`（未声明许可证 —— 视为「保留所有权利」）

- 提供 Java 库 + SVG/PNG 渲染 + Gerber X2/Excellon 转换。
- README 声称 *"The whole model is normalised to millimetres at parse time"* ——
  **与本项目 AGENTS 约束 4 的目标一致**，可作为设计验证的旁证。
- **样本来源**：`examples/*.tgz`、`minimal-test-odb.zip`。
- ⚠️ **无许可证声明 = 默认保留所有权利**。仅作阅读与样本获取，**不得复制任何代码或文档文本**。

### 4.5 `capablemonkey/odb-pp-parser`（未声明许可证）

- 只解析 `features` 与 `netlist`，输出 JSON。
- 其 JSON 结构可作为「人类可读」目标的命名风格参考，同样**不得复制代码**。

---

## 5. ⚠️ 社区二次文档的准确性警告

调研中发现社区文档存在**与官方规范冲突的实质性错误**，必须避免以其为依据：

| 出处 | 错误说法 | 官方规范（本文档 `01`） |
|---|---|---|
| OdbDesign `docs/reference/research/Features Shapes.md` | 「Coordinates: Integer-based in micrometers or mils (e.g. `1000 2000`)」 | 坐标是**小数**，`UNITS=MM` 用 mm，`UNITS=INCH` 用 inch（p.23-25） |
| 同上 | 「格式使用 UTF-8/ASCII ... 记录以换行终止」并给出 `.sym` 文件示例 | ODB++ **没有** `.sym` 文件；自定义符号是 `symbols/<name>/features`（p.37） |
| 同上 | 「Attributes: `ROT=90`, `NET=signal1`」 | 属性通过 `@`/`&` **索引表**间接引用，**不是**内联 `KEY=VALUE`（p.168） |
| OdbDesign `docs/reference/research/ODB_SHAPE_DEFINITIONS.md` | 「Parameters: dimensions embedded in the name (in 1/10 microns)」 | 符号参数单位是 **µm 或 mil**，不是 1/10 µm（p.36） |
| 同上 | 「Via (V)」「Component (C)」被列为 features 图元 | features 只有 `P`/`L`/`A`/`S`/`T`/`B`（p.40, p.169-180）；via 由 `features` 的 pad + `tools` 的 tool 表达 |
| 同上 | 「Symbols: reusable shapes stored in `.sym` files」 | 同上的 `.sym` 不存在 |

**结论：以 `docs/ODB++_Format_User_Guide.pdf`（8.1 Update 4）为唯一权威依据。
社区文档只用于「了解别人的工程取舍」，不得用于确认格式语义。**

> 附带发现：OdbDesign 仓库自己也在 `docs/reference/odbpp/odb-spec.md` 中强调
> *"Use the spec as the source of truth for these assumptions."* —— 与本节结论一致。

---

## 6. 技术选型建议（基于以上调研）

| 关注点 | 建议 | 理由 |
|---|---|---|
| 归档/压缩 | 标准库 `tarfile` / `zipfile` / `gzip`，**按 gzip 魔数探测** | 零依赖；`01` §2.3 |
| 多边形布尔运算 | **`pyclipper`**（主）+ **`shapely`**（几何校验/拓扑分析） | OdbDesign 自己不做布尔运算；`pyclipper` 是 Clipper 的绑定，整数坐标、鲁棒、快；`shapely` 适合做自交检测、面积/包含关系验证 |
| 坐标精度 | 内部统一为 **整数**（单位 1e-6 mm）再交给 `pyclipper` | Clipper 需要整数坐标；规范精度要求 1/400 µm ≈ 2.5e-6 mm，1e-6 mm 网格足够 |
| 圆弧离散化 | 自研（弦高误差控制），复用同一实现处理 `A`/`OC` | `02` §3.4；两种语境（层轮廓、符号内部）都需要 |
| 归档抽象 | 不引入 `libarchive-c`（除非确需更多格式） | 减少依赖；v8.1 只需 tar/zip/gzip |
| XML 部分（stackup/impedance） | 本阶段**不做** | `01` §6 |

### 6.1 明确不引入的东西

- **不引入** OdbDesign 的 `protobuf`/`grpc` 体系：那是为 C++ 服务化设计的，
  Python 侧用 dataclass + `pydantic`（或纯 dict）即可，且避免 AGPL 牵连。
- **不引入** KiCad/C++ 侧的几何库：本项目是 Python 技术栈。
