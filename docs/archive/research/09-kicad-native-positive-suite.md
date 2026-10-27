# 09 · KiCad 10 原生小型正向案例集审定

实施日期：2026-09-28。依本仓库 `AGENTS.md`，在首次生成前重新读取
06、07、08 调研报告；当时尚无 09，故本文补录实际实现与审定结果。
已有 `cases/` 中的公开 v8.1 样本、`external/` 开源调研和本地官方
*ODB++Design Format Specification*, Release 8.1 Update 4 均可复用，
无需重新下载。本文的格式解释以本地官方 PDF 为准。

## 工具链与 ABI

| 项 | 本机实测 |
|---|---|
| `kicad-cli.exe` | `E:\Program Files\KiCad\10.0\bin\kicad-cli.exe`，`version` → 10.0.6 |
| KiCad Python | 同目录 `python.exe`，CPython 3.11.5、64-bit、MSC v.1944 |
| KiCad `pcbnew` | 同目录 `Lib\site-packages\pcbnew.py`，`GetBuildVersion()` → 10.0.6 |
| 项目 uv | uv 0.12.19；`.venv` 为项目解析和 pytest，未装 PyPI 同名 `pcbnew` |

`uv sync --all-extras` 已在根目录运行。受当前沙箱的用户目录权限限制，
运行时把 `KICAD_DOCUMENTS_HOME`、`KICAD_CONFIG_HOME` 和 `UV_CACHE_DIR`
定向到仓库的忽略目录 `build/`。CLI 会尝试写 `HKCU\Software\kicad-cli`
并打印权限诊断，但七次原生导出均返回 0，ZIP 均可完整解压、
`misc/info` 声明 v8.1，且经本项目解析无 error 级诊断。

## 案例与审查

六块源板来自 [`generate.py`](../../../cases/kicad-positive/generate.py)，
其中贯穿孔板分别导出 MM、INCH。全部原生导出来自 KiCad CLI
`pcb export odb --precision 6 --compression zip`，未后改 ZIP 内容。
生成命令及逐板说明见 [案例集 README](../../../cases/kicad-positive/README.md)。

| 导出名 | 原生 matrix 的实体层 | 主要 features | 钻孔跨度 |
|---|---|---|---|
| `outline-only-mm` | 2 SIGNAL、1 DIELECTRIC | 铜 `F 0`、profile `F 1` | 无 |
| `pad-shapes-mm` | 2 SIGNAL、1 DIELECTRIC | F.CU `F 4`，r/rect/oval | 无 |
| `tracks-arcs-mm` | 2 SIGNAL、1 DIELECTRIC | F.CU `F 2`，L/A | 无 |
| `through-hole-mm` / `-in` | 2 SIGNAL、1 DIELECTRIC、1 DRILL | 双面 r800 铜环，r400 孔；英制 mil 等价 | F.CU→B.CU |
| `zone-hole-rotation-mm` | 2 SIGNAL、1 DIELECTRIC | F.CU `F 2`，P/S | 无 |
| `four-layer-drills-mm` | 4 SIGNAL、3 DIELECTRIC、3 DRILL | 三钻孔层各 `F 1` | F.CU→B.CU、F.CU→IN1.CU、IN1.CU→IN2.CU |

所有 step profile 都是 20 × 10 mm 闭合矩形。KiCad 10 即使
`SetEnabledLayers` 只主动选铜和 Edge.Cuts，保存时仍强制加入空的
Margin 和双面 Courtyard。原生包包含这些层及组件/辅助目录，
`native/` 完整保留。`clean/` 由可审计的
[`curate.py`](../../../cases/kicad-positive/curate.py) 产生：仅删重复的
Edge.Cuts 文档层、空组件/文档层及无关辅助文件，并重排 matrix `ROW`；
物理层的 `features`、`attrlist`、`tools` 和 step `profile` 逐字节不动。
裁剪器对删除层图元计数、保留钻孔 tools 和潜在自定义符号进行检查，
发现预期外数据即失败。两组名称明确区分，避免把裁剪包误称原生导出。

填充区板的源 `zone` 含独立闭合内孔。KiCad 导出 `S` 图元时，
用往返重合的零宽裂缝把内孔连接到外边界；解析器做拓扑规整后
恢复一个 `island=false` 的空洞 ring，实体面积约 57.997 mm²。
这是真实 KiCad 表示，不是为了覆盖负极性手写修改的导出物。

公英制贯穿孔板的 XY、孔径与多边形面积经 mm 归一化一致。
两包均写 `.copper_weight=1.0`，但依官方规范，其物理意义随
`UNITS` 改变：MM 是 1 µm，INCH 是 1 oz/ft²。输出按仓库规定
为英制注明 8.96 g/cm³ 假定密度并计算约 0.0340571 mm；
因此绝对 Z 两版不同，这是正确的单位语义，不应强行做 Z 相等断言。
KiCad 还在铜层写 `.layer_dielectric`，测试不把它用作铜厚。

## 验证边界

`tests/test_kicad_positive_cases.py`（原路径 `../../tests/test_kicad_positive_cases.py`）
以源板设计数值与面积公式为独立预期，覆盖 v8.1、profile、几何、
mm/in、孔跨度、介质扣孔、实体正向多边形与 Z 元数据；同时核对
干净版保留文件和原生版的字节一致性。普通 pytest 不调用 KiCad。
本次新案例测试为 **19 passed**；全仓库使用新的临时目录运行
`uv run --all-extras pytest -p no:cacheprovider --basetemp=build/pytest-kicad-suite-0928`
得到 **590 passed**。默认 `build/pytest-tmp` 和 `.pytest_cache` 有历史
权限问题，故本次完整回归显式使用新目录并禁用 pytest 缓存。
七份干净包也都通过 `odbpp export` 实际生成 JSON、SVG、HTML，
检查输出均为 mm 单位且含预期层数。
KiCad 没有生成负极性、层级 profile、递归自定义符号，相关验证
继续由 `tests/fixtures/` 的原有手写夹具承担。

重新导出时，KiCad 的 UUID、时间戳与图元顺序可变；应检查
结构和几何语义，而非整包哈希。此套案例面向本项目开发回归，
不替代生产板兼容性测试。
