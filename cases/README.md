# cases/ — ODB++ v8.1 测试用例清单

本目录存放用于开发与回归验证的 ODB++ **v8.1** 输入样本。所有样本的 `misc/info` 均声明
`ODB_VERSION_MAJOR=8` / `ODB_VERSION_MINOR=1`，即符合本项目锁定的目标格式版本。

新增的 [KiCad 小型正向案例集](kicad-positive/README.md)含六块独立源板、
七份原生导出 ZIP（其中一块板同时导出 MM/INCH）及七份单独标明
事后裁剪的干净 ZIP。每块板的设计参数与几何预期位于
[`kicad-positive/notes/`](kicad-positive/notes/)，普通 pytest
通过 `tests/test_kicad_positive_cases.py` 只读取这些固定 ZIP。

> 分析结论与逐例观察详见 `docs/research/03-case-inventory.md`。

## 1. 清单

| # | 用例 | 形态 | 单位 | 来源工具 | 层数 | 铜层 | 介质层 | 钻孔层 | 自定义 symbol | Step |
|---|------|------|------|----------|------|------|--------|--------|---------------|------|
| 1 | `minimal-test-odb/` | 展开目录 | `MM` | ODB++ Test Generator | 2 | 2 | 0 | 0 | 0 | `pcb` |
| 2 | `kicad-kitchen-sink/` | 展开目录 | `MM` | KiCad EDA 9.99 | 37 | 2 | 1 | 1 | 0 | `pcb` |
| 3 | `kicad-video-odb.tgz` | tar+gzip | `MM` | KiCad EDA 9.0.3 | 28 | 4 | 3 | 1 | 0 | `pcb` |
| 4 | `designodb-rigidflex.tgz` | tar+gzip | `INCH` | Xpedition xPCB Layout → Valor NPI 10.0 | 43 | 10 | 9 | 7 | 123 | `cellular_flip-phone` |
| 5 | [`kicad-positive/`](kicad-positive/README.md) | 7 对原生/干净 ZIP | `MM`/`INCH` | KiCad EDA 10.0.6 | 每包 3–10 物理层 | 2/4 | 1/3 | 0/1/3 | 0 | `PCB` |

覆盖矩阵（既有用例 1/2/3/4，新小型案例集见上方独立清单）：

- **单位制**：Metric（1、2、3）、Imperial（4）→ 覆盖 AGENTS 约束 4 的双向换算。
- **输入形态**：展开目录（1、2）、压缩包（3、4）→ 覆盖 AGENTS 数据流入口的两种形态。
- **层类型**：`SIGNAL` / `POWER_GROUND` / `DIELECTRIC` / `SOLDER_MASK` / `SOLDER_PASTE` /
  `SILK_SCREEN` / `DRILL` / `ROUT` / `COMPONENT` / `DOCUMENT` / `MASK` → 覆盖 spec 表 5-2 的绝大多数基类型。
- **层子类型（ADD_TYPE）**：`COVERLAY`(SOLDER_MASK)、`PG_FLEX`(POWER_GROUND)、
  `BEND_AREA`/`FLEX_AREA`/`RIGID_AREA`/`STIFFENER`(MASK) → 覆盖刚挠结合与增强板子类型。
- **钻孔跨度**：通孔（`D_1_10`）、盲孔（`D_1_2`、`D_2_3`、`D_8_9`、`D_9_10`）、埋孔（`D_3_8`）、
  挠性区埋孔（`D_5_6`）→ 覆盖约束 3「与介质层 Z 区间有重叠的钻孔投影」的全部情形。
- **符号**：标准符号（`r`/`s`/`rect`/`oval`/`donut_r`/热焊盘等）+ 123 个自定义符号（用例 4）
  → 覆盖约束 5「基础标准符号原生展开 + symbols/ 递归读取」。

## 2. 各用例定位

### 2.1 `minimal-test-odb/` — 最小冒烟用例

仅含 2 个铜层，无介质层、无钻孔、无自定义符号；但已有 21 个焊盘、6 条线和 2 条圆弧。用于验证 Parser 骨架、基础二维图元、单位换算与 JSON 输出的最小闭环。

### 2.2 `kicad-kitchen-sink/` — 主开发用例（功能覆盖最广）

KiCad 自家 ODB++ v8.1 导出器的「全特性」输出，含 26 个 `DOCUMENT` 层（`edge.cuts`、`margin`、
`user.*`、`f.adhesive`、`f.fab`、`f.courtyard` 等）。同时包含若干**仅存在于磁盘、未出现在 matrix 中**的
层目录（`capping_f.cu-b.cu`、`filling_f.cu-b.cu`、`plugging_front`、`tenting_front` 等），
是检验「matrix 与文件系统不一致时如何取舍」的天然夹具。

`dielectric_1/features` 的 `F 0`（特征数为 0）也印证了约束 3 的前提：
**KiCad 导出的介质层本身不携带几何**，其基底多边形只能由板框推导。

### 2.3 `kicad-video-odb.tgz` — 真实中等规模 multilayer 板

4 层铜 + 3 层介质，包含 `in1.cu` / `in2.cu` 内层与 `top_copper`，用于验证多层堆叠与内层介质区间映射。

### 2.4 `designodb-rigidflex.tgz` — 生产级刚挠结合板（最严苛）

真实的手机刚挠结合板设计（Xpedition 输出、Valor NPI 存档），43 层 / 10 铜层 / 9 介质层 /
7 个钻孔层 / 123 个自定义符号。**单位制为 INCH**，是约束 4 单位换算的主要依据，也是
`sym` 尺寸以 mil 表达、坐标以 inch 表达这一「双单位并存」规则的实据。

## 3. 来源与授权

| 用例 | 上游 | 说明 |
|------|------|------|
| `minimal-test-odb/` | `mcix/odbpp` → `odbpp-lib/src/test/resources/odb/minimal-test-odb.zip` | 上游标注为自建测试数据 |
| `kicad-kitchen-sink/` | `FixturFab/ODB2kicad` → `samples/odb-kitchen-sink` | KiCad EDA (GPL-3.0) 导出产物 |
| `kicad-video-odb.tgz` | `mcix/odbpp` → `examples/video-odb.tgz` | KiCad EDA (GPL-3.0) 导出产物 |
| `designodb-rigidflex.tgz` | `FixturFab/ODB2kicad` + `mcix/odbpp` → `designodb_rigidflex.tgz` | ODB++ 官方公开示例板（DesignODB），跨仓库重复分发 |

本目录样本**仅用于本地解析器的开发与回归测试**，不随本项目的任何发布产物再分发。
若后续需要对外发布，需先逐个复核上述上游的再分发条款。

## 4. 使用约定

1. 用例为**只读输入**，任何解析过程都不得就地修改样本内容（`.sum` 校验文件依赖原始字节）。
2. 展开目录型用例在 Windows 上大小写不敏感、在 Linux 上敏感；**不要**用大小写不敏感的文件操作
   去做层名匹配验证，否则会掩盖真实缺陷（详见研究文档 03 的「风险」小节）。
3. `out/` 下每份输入对应一个子目录。四个既有样本以 `cases/` 下
   的名称命名；KiCad 小型案例分别对应
   `out/kicad-positive/native/<ZIP 文件名去扩展名>/` 和
   `out/kicad-positive/clean/<ZIP 文件名去扩展名>/`。
   `native/` 为未经修改的原生 ZIP，`clean/` 为事后裁剪 ZIP，两组结果不混用。
   运行 `uv run --all-extras python tools/sync_out.py --px 400 --py 400 --min-cell-mm 0.1 --max-cell-mm 5 --heatmap-grid --prune`
   补齐缺失预览、MaterialMap CSV、逐层掩膜/热图与索引，并清理不再对应案例的目录；
   代码变更后加 `--force` 更新全部产物。
