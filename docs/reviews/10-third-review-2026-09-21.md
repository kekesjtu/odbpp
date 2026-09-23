# 第三次审阅（2026-09-21）

基线：`66bb1d4`。审阅 `6af33bc..66bb1d4` 的几何修复、分辨率传递、绝对 Z 和铜密度换算；按新版 AGENTS.md 第 8、9 条验收。本次仅增加复审报告及复现脚本，不修改实现。

上次两个 P1 几何问题的独立用例已通过：窄颈多边形面积为 8 mm²，0.1 mm 线宽整圆有一个孔。JSON 默认继承预览分辨率、显式改变精度时重新规整的基本路径也已修复。**本次确认三个 P2 问题，仍需处理后再关闭输出一致性和 Z 标记验收。**

## 1. [P2] 缺少厚度的非结构实体层被标为已知

位置：`src/odbpp/stackup.py:447-449`。

`known` 只在缺厚度且 `_is_structural(layer)` 为真时变为 false，然后直接作为当前层的 `thickness_known`。这样做可以避免未知丝印/锡膏厚度污染后续铜层位置，但同时把该层自身假定的零厚度标成已知，违反 AGENTS 第 8 条“厚度数据缺失时显式标记 thickness_known=false”。

实际样本复现：kitchen-sink 的 F.PASTE 有 **2 个实体环**，`thickness_mm=null`，输出却为：

```json
"z_mm": {
  "start_mm": 0.0,
  "end_mm": 0.0,
  "thickness_mm": 0.0,
  "thickness_known": true,
  "source": "unknown"
}
```

SVG/HTML 也使用此标记，因此不会显示该层范围包含假定。现有 `test_a_decorative_layer_does_not_take_the_stack_down_with_it` 把装饰层自身的 `thickness_known=true` 也断言为正确，混淆了两件事。

建议：分别维护“上方结构累计位置是否可靠”和“当前层厚度是否有来源”。未知非结构层自身须为 false，但不必使其后的铜层位置变为未知。增加带实体、缺厚度的锡膏/丝印测试。

## 2. [P2] 默认写出 SVG/HTML 仍丢失预览精度

位置：`src/odbpp/svg.py:661`，以及 `render_svg()` 的第 214-215 行。

`write_outputs()` 已让 JSON 跟随 `preview.round_digits`，但 SVG 仍无条件使用 `SvgOptions()` 的默认四位精度；`render_svg()` 直接逐点格式化原始 rings，也没有使用 JSON 的重新规整逻辑。因此同一次默认写出的 JSON 与预览可表示不同实体，HTML 继承同一问题。

复现：0.00002×1 mm 的合法矩形，以 `PreviewOptions(round_digits=6)` 构建，调用 `write_outputs(preview, job, temp)`。JSON 保留 x=0.00002，SVG/HTML 却输出：

```text
M0 0L0 0L0 1L0 1Z
```

图形宽度已经为零。若用户显式指定不同的 `SvgOptions.round_digits`，同样没有重新规整；因此仅修改默认值还不够。

建议：三种格式共享最终目标分辨率和规整后的区域。未指定绘图精度时继承预览精度，指定不同精度时重新规整，并同步面积、环数量等元数据。测试应读取 write_outputs 生成的 JSON、SVG 和 HTML 并对照，而非只检查 JSON。

此问题通过公共 API 复现；默认 CLI 对各阶段传入相同 digits，不能据此断言当前默认 CLI 的三份现有样本预览已经发生同样差异。

## 3. [P2] 改变 JSON 精度后 step 汇总仍描述旧模型

位置：`src/odbpp/export.py:595-609`。

显式要求较低精度时，`layer_to_json()` 会重新规整并删除退化环，但 step 的 `bounds_mm`、summary.polygon_count、summary.empty_layers 仍从原始 preview 获取。它们不再描述实际写出的几何。

同一窄矩形以六位精度构建，再执行 `step_to_json(job, preview, round_digits=4)`：

- 层内 `polygons=[]`、`area_mm2=0`、`bounds_mm=null`；
- summary 却为 `polygon_count=1`、`empty_layers=[]`；
- step 的边界仍为 `[0,0,0,1]`。

下游依赖 summary 判断内容或选择空层时，会得到与 layers 矛盾的结果。建议先生成全部最终层数据，再从最终数据派生 step 边界、总环数、空层列表；同一次重新规整不应在多个输出字段中各算一次。

## 验证记录与范围

- 全量测试：**541 passed in 293.18s**。命令：`conda run -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/review3-pytest`。这些通过项尚未覆盖本文三个反例。

- 独立旧用例：`conda run -n odbpp python tools/review_followup.py`。窄颈面积 8.0；整圆面积约 3.140548 mm²、孔数 1；默认 JSON 的窄矩形面积 0.00002 mm² 且有效。
- 旧产物检查器：`conda run -n odbpp python tools/review_current_project.py`。本次读到 kitchen-sink、video、rigidflex 三例，分别 37/28/43 层，均无无效环；SVG XML 可解析。当前 out/minimal-test-odb 不存在，脚本跳过了它，不能把该结果表述为四例已有产物检查均通过。最小输入仍通过临时 CLI 和内存 API 用例验证。
- 本次没有重新导出四例，也没有执行浏览器交互或独立 CAM 对照。已有产物有效性检查不证明任意输入的形状均正确。
- 绝对 Z 已进入 JSON 与 SVG/HTML；英制铜重已根据默认密度换算，JSON 保留物理量、单位及密度假设。本次未发现该换算公式的新错误。局部叠层和缺失厚度的 best-effort 范围仍按修复文档披露的限制理解。

新增问题的复现：

```powershell
conda run -n odbpp python tools/review_third_pass.py
```

输出为 `out/review3-evidence.json`，临时导出自动清理，cases/ 不变。该脚本是审阅证据收集器，不是实现修复。
