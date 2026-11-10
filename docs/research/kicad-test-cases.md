# KiCad 小型正向案例

## 输入与独立预期

本地 `cases/kicad-positive/` 保留源板、原生导出 `native/`、整理版 `clean/` 以及生成 / 导出 / 整理脚本。
源板均为 20×10 mm，源设计参数和几何公式提供独立预期。
工具链证据记录 KiCad 10.0.6 及其配套 pcbnew Python ABI；项目解析与测试使用自身 uv 环境。

| 导出 | 主要内容 | 按源设计核对的预期 |
| --- | --- | --- |
| outline-only-mm | 空铜层与矩形 profile | 面积 200 mm²、两铜一介质 |
| pad-shapes-mm | 圆 / 矩形 / 长圆焊盘 | 位置、符号尺寸、方向 |
| tracks-arcs-mm | 直线与圆弧 | 线宽、弦高误差及实体面积 |
| through-hole-mm / in | 双面 r800 铜环、r400 孔 | 0.8 / 0.4 mm 尺寸与公英制 XY 一致 |
| zone-hole-rotation-mm | 区域、内孔及旋转 | 规整后显式孔与源设计方向 |
| four-layer-drills-mm | 四铜三介质，三种钻孔跨度 | 通孔、盲孔与埋孔仅覆盖对应介质 |

原生填充区以往返零宽裂缝连接内孔和外边界，Parser 规整后恢复孔环。
中间 JSON 的介质保留完整基底，SVG/HTML 预览在绘图副本中扣除相交钻孔，等效材料采样将相交的钻孔投影视为实心铜。核对面积和孔环数时应注明产物阶段。

## 原生与整理版

原生 ZIP 保留 KiCad 导出的输入。整理版移除重复或空辅助层及无关文件并重排 ROW，保留板轮廓、物理层图案、厚度属性和钻孔工具语义。重新生成时按源设计核对结构、单位、几何和层跨度。

## 单位与厚度

KiCad 两种单位导出均可能写 `.copper_weight=1.0`；按规范 MM 表示 1 µm，而 INCH 表示 1 oz/ft²。
因此可要求 XY / 孔径归一化一致，绝对 Z 应按各自物理量与记录来源核对。
核对厚度时使用导出文件实际提供的数据。缺少可信厚度时，核对估算值及其 `false` 可信状态。

## 维护入口与覆盖边界

本地案例目录中的 README 和脚本维护指定工具链的再导出流程；CLI 使用 `pcb export odb --precision 6 --compression zip`，生成后核对 misc/info 的 v8.1 声明。
这组 ZIP 用于在 `main` 上核对 KiCad 输入的正常解析和等效路径，当前默认 pytest 不读取这组 ZIP。`reference` 分支的 `tests/integration/test_kicad_positive_cases.py` 使用本地 ZIP 核对 Python 解析器，无需安装 KiCad。
负极性、递归和无效层轮廓等场景可通过[手写夹具](../../tests/fixtures/README.md)独立复现。`main` 的样本自动测试范围见[参考案例清单](case-inventory.md#手写夹具与自动测试)。
