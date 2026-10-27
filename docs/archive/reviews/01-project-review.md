# 01 · 当前项目审阅与四例导出报告（2026-09-21）

审阅基线：`0e1c5db`，同时按当前工作区的 AGENTS.md 需求验收。该文件在任务开始时已有用户修改，本次未改动、未提交它。本次生成产物并报告问题，不修改解析器实现。

## 验证范围与复现

- 复用现有四个 cases、官方规范、五份调研材料和 external/OdbDesign；使用本机 conda 环境 `odbpp`。
- 四例均执行完整导出，不排除 DOCUMENT，保留默认几何参数：弓高 0.005 mm、坐标四位小数、零宽线不增宽。
- 全量测试：`conda run -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/review-pytest-20260921`，结果 **472 passed in 164.55s**。
- 复现和产物检查：`conda run -n odbpp python tools/review_current_project.py`，证据写入 `out/review-evidence.json`。脚本验证 JSON 可读、逐个 SVG 的 XML 合法性、输出环的有效性，并计算介质与钻孔的平面重叠。平面重叠本身不判定盲埋孔的 Z 跨度；下文真实样本定量结论使用明确的通孔层。
- 未进行与独立 CAM 软件逐层像素/几何对照，也未进行浏览器交互验收；测试通过不等于实体正确性验收通过。

导出命令模板（`<case>` 分别为四个输入名）：

```powershell
conda run -n odbpp python -m odbpp export cases/<case> -o out/<case> --per-layer-svg
```

最终产物：

| 输入/输出子目录 | 层数 | 输出环数 | SVG 数（含总图） | warning 数 | 无效或零面积环 |
|---|---:|---:|---:|---:|---:|
| minimal-test-odb | 2 | 26 | 3 | 0 | 0 |
| kicad-kitchen-sink | 37 | 148 | 38 | 23 | 0 |
| kicad-video-odb.tgz | 28 | 17544 | 29 | 26 | 0 |
| designodb-rigidflex.tgz | 43 | 69895 | 44 | 17 | 6100 |

每例包含总 JSON、总 SVG、自包含 HTML、layers/ 下逐层 SVG 和 export.log。四次导出退出码均为 0，error 级诊断和 failed_layers 均为空；114 个 SVG 均通过 XML 解析。空层保留在模型中，详见 JSON 的 summary.empty_layers。刚挠板总 JSON 约 126 MB，HTML 约 34 MB，打开可能较慢。旧的刚挠板三份同名总文件已经用本次完整导出替换。

## 已确认的问题（按优先级排序）

### 1. [P1] 介质实体没有扣除与其相交的钻孔投影

位置：`src/odbpp/expand.py:717-726`、`src/odbpp/expand.py:742-752`。

`expand_layer()` 以 profile 填充介质，仅应用该层自己的 features；没有读取其他钻孔层、判断钻孔跨度或执行相减。介质 features 为空时直接返回板框。因此本次生成的 JSON/SVG 中，钻孔层虽然单独显示，介质仍然填满对应孔洞，不符合当前 AGENTS 约束 3。

真实复现：video 样本的 DIELECTRIC_1/2/3 都输出 28029.282651 mm²，分别与通孔层 DRILL_PLATED_TOP_COPPER-B.CU 重叠约 **558.687139 mm²**；kitchen-sink 的介质与 DRILL_PLATED_F.CU-B.CU 重叠约 **5.850287 mm²**。数据见证据文件的 `dielectrics`。

建议：建立层叠与钻孔跨度模型，按 Z 区间重叠选取 Drill，将投影合并后从介质基底相减；缺少厚度或跨度时显式诊断，不猜测全板贯穿。补充通孔、盲孔、埋孔、层级 profile 和无有效跨度的回归用例。

### 2. [P1] JSON 缺少可重建的层叠信息，属性单位作用域丢失

位置：`src/odbpp/job.py:319-323`、`src/odbpp/export.py:140-154`、`src/odbpp/export.py:358-388`。

`parse_attrlist()` 返回属性及单位作用域，但 `_load_layer()` 丢弃作用域；`layer_attributes()` 将属性原字符串写出。输出只有层顺序 row 等描述，没有规范化厚度、Z 区间、钻孔 start_name/end_name 等。`copper_weight()` 虽已实现，却未接入这条导出路径。

真实复现：INCH 刚挠板 DIELECTRIC_3 的 `.layer_dielectric` 仍为字符串 `0.00255906`，按 inch 换算应为约 **0.065000124 mm**，而文档顶层标注 `units: mm`。虽然 source 保留 job 输入单位，属性文件自身的单位覆盖已丢失，下游不能可靠恢复局部语义。铜厚/铜重也只有未经解释的属性，不能把英制 oz/ft² 直接当成长度。

建议：保留每个属性文件的单位作用域，输出明确的公制厚度、材料属性、Z 区间和钻孔起止层；铜重保留物理量类型和单位，缺乏可靠厚度依据时输出未知并诊断。原始属性可独立存为带单位的溯源数据，避免与规范化值混淆。

### 3. [P1] 合法整圆弧被静默丢弃

位置：`src/odbpp/expand.py:411-423`。

`_path_points()` 对起点等于终点的整圆返回两个相同点；后续 stroke 路径去重后不足两点，返回空区域。该情形应按整圆离散化，代码自身和现有研究文档均明确起终点重合代表 360°。

最小复现：中心 (0,0)、起终点 (5,0)、`r1000`、MM 文件的圆弧，应形成半径 4.5–5.5 mm 的环，理论面积约 **31.415927 mm²**；实际为 **0**，diagnostics 为空。复现脚本的 `full_circle` 固定验证此问题。

建议：生成完整圆的中心路径，保留闭合段，分别验证顺/逆时针与单图元/批处理入口。

### 4. [P1] 输出坐标四舍五入后没有重新验证拓扑

位置：`src/odbpp/export.py:342-343`、`src/odbpp/export.py:379-386`。

内部布尔几何分辨率为 1e-6 mm，但输出默认变为 1e-4 mm；直接逐点取整会合并相邻点、压扁窄环或使边相交。导出不重新清理或验证，原有 area_mm2 也仍来自取整前几何。因此消费者可能拿到面积为零或无效的“规范化多边形”，却没有对应诊断。

刚挠板实际输出有 **6100** 个无效或零面积环；例如 SPT 的第 2183 号环面积为 0，SIGNAL_8 第 1 号环面积非零但几何无效。具体数量与层/环索引记录在 `out/review-evidence.json` 的 `invalid_ring_count` 和 `invalid_ring_examples`（环索引从 0 开始）。这些输出缺少最终有效性检查；尚未逐一追溯全部 6100 个环的上游成因，不能认定它们全由取整造成。

独立取整复现：有效的 0.00002×1 mm 矩形传入实际 `_points_json(..., 4)` 后，宽度变成 0，输出多边形无效。该例用于隔离序列化缺陷，并非主张此宽度是正常 PCB 设计尺寸。不把整个预览的成功退出当作拓扑合格证据。

建议：在目标精度下重新量化、布尔规整并验证后序列化，面积与边界基于最终坐标重算。对退化环明确诊断；仅增加小数位不能替代拓扑验证。补充序列化后再读取的端到端有效性测试。

### 5. [P2] 公开 intersection API 的空集合和多参数语义错误

位置：`src/odbpp/clipper.py:327-338`。

函数先过滤空 Region，使 `A ∩ 空集` 错误返回 A；三个以上区域时将后续区域一起作为 clip，相当于 `A ∩ (B ∪ C)`，不是声明的全部交集。

复现：A=[0,2]×[0,2]，B=[0,1]×[0,2]，C=[1,2]×[0,2]。`intersection(A, Region())` 和 `intersection(A,B,C)` 的面积都应为 0，实际都为 **4 mm²**。这是公开 API 问题；现有生产导出未调用此 API，不能据此断言已污染四例导出。

建议：任何输入为空时返回空；对多个输入逐一求交。现有测试覆盖零参数、单参数及两个非空区域，缺少这两个边界条件。

### 6. [P2] 基础安装漏声明启动必需的 pyclipper

位置：`pyproject.toml:13-18`、`src/odbpp/__init__.py:56`、`src/odbpp/clipper.py:33`。

`dependencies=[]`，pyclipper 只在可选 geometry extra 中，但包初始化无条件导入 clipper，连 `info`、`summarize` 这类解析命令也无法在没有 pyclipper 的基础安装中启动。README 的 `pip install -e .` 不会补齐依赖。本机环境已经装好依赖，掩盖了新环境问题。

通过独立 Python 进程的 import hook 模拟缺少 pyclipper，`import odbpp` 直接抛出 ModuleNotFoundError；未修改或卸载本机环境中的包。

建议：把 pyclipper 声明为核心依赖，或真正隔离几何模块的导入，并在导出时给出 extra 安装指引；增加干净环境安装冒烟验证。

### 7. [P2] --no-diagnostics 没有从 JSON 中移除诊断

位置：`src/odbpp/cli.py:411-431`、`src/odbpp/svg.py` 的 `write_outputs()`。

参数帮助承诺省略 JSON 的 diagnostics 数组，实际参数未传给 `step_to_json(include_diagnostics=...)`；直到文件写完才检查标志，只影响终端错误打印。复现脚本通过真实 CLI 导出最小样本，标志开启后 JSON 仍含 diagnostics 键。

建议：将选项沿 write_outputs 传递到 JSON 序列化，并验证最终文件的键缺失，而非只检查退出码。

## 验收结论与修复顺序

当前项目具备可运行的解析、合成和预览闭环，测试覆盖较广；但“生成了文件”和“物理实体已正确重建”尚不能等同。建议先修复整圆、介质扣孔和层叠单位，再解决最终精度拓扑；随后修正基础集合操作、安装依赖与 CLI 参数。当前四例输出保留程序真实行为，可用于工程核对和缺陷复现，不能直接视为符合全部 AGENTS 约束的实体模型。

本次将问题复现脚本保存在 tools/，便于修复后对照；它是审阅证据收集器，不是已加入 pytest 的回归测试，也不自动修补或清理输出几何。
