# 导出性能审阅与优化（2026-09-22）

后续状态：本文确认的三项既有问题已修复，见 [14-review-fixes-2026-09-22.md](../fixes/14-review-fixes-2026-09-22.md)。本文保留当时的审阅与基准记录。

基线：`71ab51e`。复用现有四个真实样本、官方 8.1 规范及开源参考调研；本轮不改变格式语义、离散化容差、布尔拓扑、单位、叠层 Z 或诊断。

## 性能问题及处理

1. **逐层 SVG 重复扫描整板边界**。每个 SVG 都调用 `_viewbox`，而 `StepPreview.bounds` 对每层还读取两次边界。video 样本的 cProfile 记录显示 30 次 `render_svg`、约 143 万次环边界查询；写出阶段 65.58 s 中约 43.83 s 在整板边界调用链。该组数据有 profiler 开销，仅用于定位，不能当作日常运行耗时。
2. **同一环反复计算面积与边界**。JSON、SVG 属性、HTML 行、整体视口都会查询。现在只缓存 frozen `Ring`（坐标为不可变 tuple）的这两个派生量；可变的 `Region`、`LayerPreview`、`StepPreview` 不缓存聚合结果，追加/移除/替换环仍能得到实时结果。
3. **SVG 和 HTML 重复格式化相同坐标**。一次 `write_outputs` 现在共享完整绘图内容与整板视口，逐层 SVG 也使用该视口。复用仅限本次调用，精度和样式改变不会误用旧缓存。公开渲染 API 与输出选项保持不变。

未采用降低精度、跳过 DOCUMENT 层、省略规整/校验等方法。缓存增加少量每环常驻内存；本轮未量化峰值内存，也没有把预览模型改造成流式模型。

## 兼容性和耗时验证

使用 `tools/benchmark_export.py` 分别计时 load / compose / write，并对 JSON、总 SVG、HTML、逐层 SVG 保存文件长度和 SHA-256。哈希在计时结束后计算。大样本保留 DOCUMENT 层，使用默认四位输出精度。

基线使用 `71ab51e` 的源文件副本，通过进程级 `PYTHONPATH` 指定，避免切换或覆盖工作区源码。优化版在同一 `odbpp` conda 环境运行。耗时为单次测量，受系统负载、文件缓存等影响，不是稳定性统计或性能保证。

四例共 **122 个文件全部长度和 SHA-256 一致**，包括诊断、绝对 Z、坐标及格式化文本。以下包含 `--per-layer-svg`；不能把该倍率直接套用到默认三文件导出或 `--only-json`。

| 样本 | 写出前 / 后（s） | 写出提速 | 总耗时前 / 后（s） |
|---|---:|---:|---:|
| minimal-test | 0.013 / 0.010 | 1.32× | 0.024 / 0.023 |
| kitchen-sink | 0.434 / 0.091 | 4.77× | 0.667 / 0.418 |
| video | 34.553 / 7.684 | 4.50× | 55.066 / 28.811 |
| rigidflex（43 层） | 104.999 / 14.284 | 7.35× | 207.665 / 115.201 |

最大样本端到端减少约 **44.5%**。几何合成从 78.92 s 到 76.99 s，属于小幅波动，本轮收益主要来自写出阶段。小样本的毫秒级差异不宜过度解读。

测试：相关模块 **194 passed, 3 deselected**；全量 **564 passed in 250.13 s**。新增 16 项覆盖不同绘图选项组合与精度、独立渲染 API 和组合写出的内容一致性，以及读取统计后修改 Region 的正确性。

本地产物：`out/performance-optimized/` 保留本轮四例最终导出及 benchmark.json；`out/performance-baseline/benchmark.json` 保留基线计时与文件摘要。基线重复导出文件、profiler 的重复导出文件、临时基线源码副本在核对后清理，仅保留性能分析数据和最终结果。

复现优化版及兼容性检查：

```powershell
conda run --no-capture-output -n odbpp python tools/benchmark_export.py --output out/performance-optimized --per-layer-svg --compare out/performance-baseline/benchmark.json
conda run --no-capture-output -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/performance-full-tests
```

`--compare` 在任何产物内容不同或缺失时失败；不能把不同输入路径、不同选项下的文件直接作为等价基线。工具的 `--profile` 可用于独立采样，不应与计时测量并行运行。

## 审阅中确认、未纳入性能修改的问题

- **P1：HTML 图层行缺少 `>`**（`svg.py::_layer_rows`）。`data-*` 属性后直接拼接 `<input ...>`，导致 input 被解析成 div 的属性。对 minimal 样本的基线 HTML 用标准库 `HTMLParser` 解析，实际 input 数为 0；查看器随后对 `row.querySelector('input')` 的结果调用事件方法，存在初始化失败问题。现有测试主要检查字符串存在，未验证浏览器 DOM 与交互。这是本轮确认的既有功能缺陷，应另行修复并补浏览器/DOM 级回归；本次没有改变生成 HTML，以便性能提交能与基线逐字节对照。
- **P2：零位小数 SVG 数字格式错误**（`svg.py::_fmt`）。无小数点时仍执行 `rstrip('0')`，实测 `_fmt(10, 0) == '1'`、`_fmt(100, 0) == '1'`、`_fmt(-20, 0) == '-2'`。影响 `--digits 0` 或显式零位 SVG 精度的坐标、视口和 Z 属性；默认四位导出不受此问题影响。另行修复会改变对应产物，需要专门的数值回归。
- **已有的 profile 精度缺口仍在**（`export.py::_profile_json`）：step profile 逐点取整，未走层实体的重新规整流程，低精度可能产生退化。延续报告 11/12 的已知限制。
- **后续性能空间**：JSON 使用标准库缩进流式编码，合成阶段仍有大量 Clipper union 与坐标转换。本轮没有重写 JSON 编码器或几何算法，优先保证输出兼容。

本轮兼容性结论只针对已有合成测试和四个样本；不代表所有 ODB++ 输入已完成 CAM 对照或浏览器验收。

## 提交

- `3758d70`：可复现的阶段计时和产物指纹工具。
- `35a6114`：不可变环统计缓存、整板视口/绘图复用、回归测试与基线比对选项。
