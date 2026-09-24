# 06 · 简单 ODB++ 测试案例的构造与公开样本调研

调研日期：2026-09-24。目标是得到**小、可解释、能写出预期结果**的 v8.1 单板测试输入；复杂生产板仍用于兼容性回归。格式语义以本地 [官方 v8.1 Update 4 规范](../ODB++_Format_User_Guide.pdf)为准。

## 1. 两种构造方式

### A. 在 KiCad 画极简板，然后导出（首选的真实 EDA 样本）

KiCad 9 起提供原生 ODB++ 导出。PCB 编辑器菜单是 `File → Fabrication Outputs → ODB++ Output File…`，可选单位、精度和 ZIP/TGZ/不压缩目录。命令行也支持 `kicad-cli pcb export odb`。见 [KiCad PCB 编辑器手册](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)和 [KiCad CLI 手册](https://docs.kicad.org/10.0/en/cli/cli.html)。本机当前 `kicad-cli` 不在 `PATH`，下列命令是经官方文档核对的复现命令，尚未在本机执行。

建议设计一块 **20 × 10 mm、2 铜层、1 介质层**的板：

1. 新建空白 `.kicad_pcb`，在 `Edge.Cuts` 画闭合矩形 `(0,0)` 至 `(20,10)`；板框不要由未闭合的四段线组成。
2. 在板设置中指定 2 层铜、介质厚度（例如 1.6 mm）和铜厚。在 `(10,5)` 放一个双面通孔焊盘或一颗过孔，孔径 0.3 mm、铜环直径 0.7 mm；顶层放一段宽 0.2 mm 的走线。先只用圆形焊盘、直线和矩形板框。
3. 保存源板文件，使用上述菜单导出 `MM`、精度 6、`ZIP`。若要检查文本文件，另导出一次 `Compression format = None`。不要把 Gerber/Excellon 文件改名当成 ODB++。
4. 保留 `.kicad_pcb` 作为**独立预期值来源**，并核对导出物的 `misc/info` 版本、`matrix/matrix` 中的层与钻孔跨度、`steps/pcb/profile`、各层 `features`/`tools`。不同 KiCad 版本的空层与辅助层可能变化，断言应聚焦板框、图元和 Z 位置。

```powershell
conda activate odbpp
kicad-cli pcb export odb --units mm --precision 6 --compression zip -o cases\tiny-kicad-mm.zip cases\tiny-kicad\tiny.kicad_pcb
python -m odbpp summarize cases\tiny-kicad-mm.zip
python -m odbpp export cases\tiny-kicad-mm.zip -o out\tiny-kicad-mm
```

可以从**同一块源板**再导出一次 `--units in`，形成公英制等价对照：最终 JSON 的几何尺寸应在精度容差内一致，而 ODB++ 原始坐标/标准符号/钻孔刀具的单位语义不同。命令行 `--compression` 的合法值是 `none`、`zip`、`tgz`；`--units` 是 `mm`、`in`。源板与导出物都应固定 KiCad 版本，避免跨版本差异混入格式解析测试。需要实际物理厚度断言时，先确认导出物是否写出了层厚属性；若未写出，应断言 `thickness_known=false`，不能用板设置中的目标厚度充当 ODB++ 中的实测数据。

### B. 直接手写 ODB++ 文本（首选的最小语义夹具）

从现有 [minimal-test-odb](../../cases/minimal-test-odb/) 或 [fx_negative_layer](../../tests/fixtures/fx_negative_layer/) 复制**结构**，逐文件删减到目标场景；不要把真实生产板裁成一个无法解释的片段。最小的可读目录包括：

```text
tiny-odb/
├── misc/info                         # ODB_VERSION_MAJOR=8, MINOR=1, UNITS=MM
├── matrix/matrix                     # 一个 STEP；只登记真正存在的层
└── steps/pcb/
    ├── stephdr
    ├── profile                       # 闭合矩形 S/OB/OS/OE/SE
    └── layers/
        ├── top/features              # 例如 $0 r200；L 记录或 P 记录
        ├── dielectric_1/attrlist     # 若测试厚度与绝对 Z
        ├── bottom/features
        └── drill_1_2/{features,tools} # 若测试穿透孔
```

`features` 等含坐标文件写明 `UNITS=MM`。坐标用 **mm**，但 `r200` 的 `200` 和钻孔 `DRILL_SIZE=300` 用 **µm**；这正是官方规范 p.23–25、p.36、p.166 的双轨单位语义。`matrix` 的钻孔层要给出正确的 `START_NAME`/`END_NAME`；介质层若提供层级 `profile`，它优先于 step profile。手写夹具只挑一个语义点，便于精确断言。现有 [夹具说明](../../tests/fixtures/README.md)已覆盖负极性、层级 profile、自定义符号递归、循环引用和 `FORM`，无需重复造同类大夹具。

## 2. 建议的最小案例序列

| 案例 | 几何内容 | 首要断言 |
|---|---|---|
| `outline-only` | 20 × 10 mm 单矩形 profile、2 个空铜层 | 板框 bbox 为 `(0,0,20,10)`；profile 面积 200 mm² |
| `pad-and-trace` | 在上例添加 1 个 `r700` 焊盘和 1 条 `r200` 走线 | 符号直径 0.7 mm、线宽 0.2 mm；输出全正向 |
| `through-hole` | 1 个介质层、1 个贯穿钻孔，直径 0.3 mm | 介质扣孔后面积约 `200 - π·0.15² = 199.929314` mm²；允许圆弧离散化误差 |
| `same-board-inch` | 将 `through-hole` 的同一 KiCad 源板导出为 `INCH` | 与 MM 导出物的归一化 mm 几何在容差内相同；厚度来源语义按单位区别 |
| `layer-profile` | 将介质层 profile 缩为 10 × 10 mm | 介质只占 100 mm² 减对应孔；测试层级优先级 |

`outline-only` 和 `pad-and-trace` 适合手写；`through-hole` 适合 KiCad 导出并与手写版本互相校验。`same-board-inch` 应由同一源板导出，不宜仅对 MM 文本乘以 1/25.4，因为标准符号和钻孔尺寸还有 mil/µm 语义。矩阵重复拼板、复杂自定义符号和盲埋孔留给现有夹具及大型回归样本。

## 3. 网络上的成熟样本

| 来源 | 样本与本仓库状态 | 适合的用途 | 注意 |
|---|---|---|---|
| [mcix/odbpp 测试资源](https://github.com/mcix/odbpp) | `minimal-test-odb.zip` 已展开为 [cases/minimal-test-odb](../../cases/minimal-test-odb/) | 最容易逐图元检查的公开样本；100 × 80 mm，双铜层，29 个图元 | **有图元**：21 P、6 L、2 A；无介质和钻孔，不能验证 Z/孔洞 |
| [FixturFab/ODB2kicad 简单板](https://github.com/FixturFab/ODB2kicad/tree/main/samples/odb-output) | 上游 `samples/odb-output`，本仓库尚未引入 | README 称为双电阻板，含网络、元件、焊盘、走线、板框；适合 EDA 产物交叉检查 | 已核对其 `misc/info` 声明 8.1、MM；层数与图元数尚未本地普查 |
| [FixturFab kitchen sink](https://github.com/FixturFab/ODB2kicad/tree/main/samples/odb-kitchen-sink) | 已在 [cases/kicad-kitchen-sink](../../cases/kicad-kitchen-sink/) | KiCad 大范围功能兼容性 | 层多，不适合逐图元“最小”断言 |
| [mcix/odbpp examples](https://github.com/mcix/odbpp/tree/main/examples) | `video-odb.tgz` 已在 [cases/kicad-video-odb.tgz](../../cases/kicad-video-odb.tgz)；另有 `flat_hierarchy-odb.tgz` | 真实多层板；`flat_hierarchy` 可作为待普查的中等候选 | `flat_hierarchy` 尚未核对 8.1 版本与规模，不能直接列入 v8.1 回归 |
| [ODB++ 官方资源](https://odbplusplus.com/design/our-resources/) | 官方 v8.1 sample；刚挠板已在 [cases/designodb-rigidflex.tgz](../../cases/designodb-rigidflex.tgz) | 权威来源与复杂参考 | 43 层，不能当简单案例；公开下载不自动等于可自由再分发 |

对外部样本先读取 `misc/info` 版本，再统计 step、层、图元、单位、symbols 和压缩形态，记录来源链接与提交版本。`cases/` 保留真实板；手写的单点语义夹具放 `tests/fixtures/`。上游样本的许可证和再分发条件应逐项核对（见 [cases/README](../../cases/README.md)）；`mcix/odbpp` 未在现有调研中找到明确许可证，暂用于本地测试和来源引用。

## 4. 当前结论

最快可执行的组合是：**沿用已有 `minimal-test-odb` 检查二维图元 → 用 KiCad 导出一块 20 × 10 mm 双层通孔板，固定源板与导出器版本 → 为层级 profile 和错误路径保留手写夹具**。本次完成调研和现有样本核对，未在当前无 `kicad-cli` 的环境中生成新的 KiCad ODB++ 文件。
