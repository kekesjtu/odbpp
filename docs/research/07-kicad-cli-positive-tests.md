# 07 · KiCad CLI 能否生成解析器的正向测试

调研日期：2026-09-25。这里的“正向测试”指**合法 ODB++ 输入应被正确解析**；同时检查本项目要求的最终多边形均为正向实体、孔洞显式表达。格式语义仍以本地 [ODB++ v8.1 Update 4 规范](../ODB++_Format_User_Guide.pdf)为准。

## 结论

**可以，但需先有 `.kicad_pcb` 板文件。** [KiCad 10 CLI 手册](https://docs.kicad.org/10.0/en/cli/cli.html)明确提供 `kicad-cli pcb export odb INPUT_FILE`，可设置 `--units mm|in`、`--precision`、`--compression none|zip|tgz` 和输出路径。CLI 文档列出的 PCB 子命令没有“从尺寸/图元参数创建板”的命令。因此完整流水线是“设计/生成 KiCad 源板 → CLI 导出 ODB++ → 本解析器读取 → 与源板设计的独立预期值比较”。需要自动造板时，可用 [KiCad `pcbnew` Python API](https://docs.kicad.org/doxygen-python-10.0/namespacepcbnew.html)的 `CreateEmptyBoard`、`SaveBoard` 等方法，或先在 GUI 中人工画好一块固定的板。`pcbnew` API 与 `kicad-cli` 是两个不同接口。

这属于**生成测试夹具和端到端正向回归**。若每次 `pytest` 都调用 KiCad，测试会依赖外部安装、版本和导出器；更稳定的做法是把一块小源板、其导出的 ODB++、以及独立的预期值清单固定在仓库中，普通测试只调用本解析器。单个符号、极性、异常输入等细粒度单元测试继续使用手写夹具。

## 能覆盖什么

| 目标 | KiCad CLI 导出能提供的正向输入 | 推荐断言 |
|---|---|---|
| 基本格式和压缩 | 同一板导出目录、ZIP、TGZ | 三种输入的规范化 JSON 语义相同；`misc/info` 实测声明 8.1 |
| 单位转换 | 同一板分别导出 `mm`、`in` | 板框 bbox、焊盘直径、线宽、孔径转换到 mm 后在容差内一致 |
| 基础几何 | 矩形板框、独立圆焊盘、直线 | 符号和线宽尺寸正确；全部输出为合法多边形，无残留圆弧 |
| 叠层和钻孔 | 双铜层、单介质、一个贯穿孔 | 钻孔跨越上下铜层；介质扣孔；有数据的层给出绝对 Z 与厚度来源 |
| 预览 | 同一 ODB++ 输入 | JSON 与 SVG/HTML 层名和几何视域一致 |

现有 [KiCad kitchen sink 样本](../../cases/kicad-kitchen-sink/)已经证明这条路线有实践基础：`misc/info` 声明 ODB++ 8.1、`UNITS=MM`；`DIELECTRIC_1/attrlist` 给出 `.layer_dielectric=1.51`，上下铜层之间有钻孔层。本仓库的 [真实板加载测试](../../tests/test_job.py)和 [导出测试](../../tests/test_export.py)已检查层类型、介质厚度、钻孔跨度、扣孔、绝对 Z 与输出多边形。但该样本有 37 层，且由 KiCad `9.99` 开发版导出，适合兼容性回归，不适合作为“每个预期值都能心算”的小测试。

## 最小可执行方案

固定一块源板：矩形板框 `0..20 × 0..10 mm`，两层铜，中间一层 `1.6 mm` 介质；`F.Cu` 在 `(5,5)` 放一个**不与走线重叠**的直径 `0.7 mm` 圆焊盘，另放一段从 `(1,2)` 到 `(4,2)`、宽 `0.2 mm` 的直线；在 `(10,5)` 放一个直径 `0.3 mm` 的贯穿孔。把源板设计参数另存为人工审定的 `expected.json`（或等价的测试常量）。若通过 `pcbnew` 脚本生成源板，预期值直接来自**脚本的输入参数**，不从本解析器的输出反填。

```powershell
conda activate odbpp
kicad-cli version
kicad-cli pcb export odb --units mm --precision 6 --compression zip -o build\tiny-mm.zip cases\tiny-kicad\tiny.kicad_pcb
kicad-cli pcb export odb --units in --precision 6 --compression zip -o build\tiny-in.zip cases\tiny-kicad\tiny.kicad_pcb
python -m odbpp summarize build\tiny-mm.zip
python -m odbpp export build\tiny-mm.zip -o out\tiny-mm --only-json --digits 6
```

上述命令的参数已按 [KiCad CLI 官方定义](https://docs.kicad.org/10.0/en/cli/cli.html)核对；本机当前 shell 找不到 `kicad-cli`，因此尚未在本机执行导出。命令中的源板也是**拟创建路径**，并非已有文件。ODB++ 导出命令没有格式版本选择项：生成后必须读取 `misc/info` 确认 8.1，不能仅凭 KiCad 版本推断。生成中间文件使用 `build/`，审定后的源板和小样本再放入 `cases/`。

建议把普通 `pytest` 固定为读取审定后的样本，不在测试运行期间重新导出。另设手动或 CI 维护命令，用指定 KiCad 版本重新生成 ODB++，比较解析后的语义结果；不做 ZIP 字节或整份 `features` 文本的逐字节断言，因为导出时间、版本字段及图元顺序可能变化。KiCad 自身的 [ODB++ QA 测试提案](https://gitlab.com/kicad/code/kicad/-/merge_requests/2180)也按层集合、文件列表和容差比较导出结果，而不是简单逐字节比较。

## 独立预期值与测试边界

1. **从设计参数推导预期值。** 板框 bbox 为 `[0,0,20,10]`，面积 `200 mm²`；孤立圆焊盘 bbox 宽高应为 `0.7 mm`。若介质轮廓恰是完整矩形、仅被一个 `0.3 mm` 圆孔穿透，其面积约为 `200 - π × 0.15² = 199.929314 mm²`，误差阈值应覆盖输出网格量化与圆弧离散化。
2. **逐层检查实体性。** 不仅断言 `units=mm`，还应检查每个 polygon 合法、面积为正，外轮廓与孔洞的 `island` 标记正确；介质与穿过它的钻孔几何交集为空。这里的孔洞仍可由 `island=false` 的 ring 表达，整个层表示的是正向实体集合。
3. **Z 厚度只断言导出文件实际提供的数据。** `thickness_known=true` 时核对数值和来源字段；缺失时核对 `thickness_known=false` 与 best-effort Z。英制 `.copper_weight` 按本项目约定核对 `kind=areal_density_oz_ft2`、`unit=oz/ft2`、`thickness_source=assumed_density`、默认密度 `8.96 g/cm³` 及其推导厚度；公制时按 µm 铜厚解释。
4. **不能拿自己的解析结果当 oracle。** “先解析一次保存 JSON，以后做字符串比较”只能发现变化，不能证明第一次解析正确。人工审定的源板尺寸、独立几何公式和 KiCad 原生板数据才可作为预期依据；SVG 与 JSON 互相一致也只是输出一致性检查。
5. **KiCad 无法替代所有格式夹具。** 现有 KiCad 样本没有覆盖层级 profile、自定义 symbols 递归、负极性等关键分支（见 [样本普查](03-case-inventory.md)）；这些继续由 [手写测试夹具](../../tests/fixtures/README.md)负责。也不要为凑分支改写 KiCad 导出物后仍称其为“KiCad 原生产物”。

综上，KiCad CLI 非常适合批量生成**真实、合法、可重复的正常路径输入**。测试可信度取决于小源板的独立预期值和固定导出器版本；CLI 本身只负责转换，不能替解析器提供自动正确答案。
