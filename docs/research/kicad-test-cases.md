# KiCad 小型正向案例

## 输入与独立预期

本地 `cases/kicad-positive/` 保留源板、原生导出 `native/`、整理版 `clean/` 以及生成 / 导出 / 整理脚本。
源板均为 20×10 mm，源设计参数和几何公式提供独立预期。
工具链证据记录 KiCad 10.0.6 及其配套 pcbnew Python ABI；项目解析与测试使用自身 uv 环境。

| 导出 | 主要内容 | 独立断言 |
| --- | --- | --- |
| outline-only-mm | 空铜层与矩形 profile | 面积 200 mm²、两铜一介质 |
| pad-shapes-mm | 圆 / 矩形 / 长圆焊盘 | 位置、符号尺寸、方向 |
| tracks-arcs-mm | 直线与圆弧 | 线宽、弦高误差及实体面积 |
| through-hole-mm / in | 双面 r800 铜环、r400 孔 | 0.8 / 0.4 mm 尺寸与公英制 XY 一致 |
| zone-hole-rotation-mm | 区域、内孔及旋转 | 规整后显式孔与源设计方向 |
| four-layer-drills-mm | 四铜三介质，三种钻孔跨度 | 通孔、盲孔与埋孔仅覆盖对应介质 |

原生填充区以往返零宽裂缝连接内孔和外边界，Parser 规整后恢复孔环。
JSON 介质保留完整基底，预览副本扣孔，材料采样中孔投影为铜；分别断言这些阶段的不同语义。

## 原生与整理版

原生 ZIP 保留 KiCad 导出的输入。整理版移除重复或空辅助层及无关文件并重排 ROW，保留板轮廓、物理层图案、厚度属性和钻孔工具语义。重新生成时按源设计核对结构、单位、几何和层跨度。

## 单位与厚度

KiCad 两种单位导出均可能写 `.copper_weight=1.0`；按规范 MM 表示 1 µm，而 INCH 表示 1 oz/ft²。
因此可要求 XY / 孔径归一化一致，绝对 Z 应按各自物理量与记录来源核对。
厚度断言只使用导出文件实际提供的数据，缺失时验证 best-effort 与 false 标记。

## 维护入口与覆盖边界

本地案例目录中的 README 和脚本维护指定工具链的再导出流程；CLI 使用 `pcb export odb --precision 6 --compression zip`，生成后核对 misc/info 的 v8.1 声明。
KiCad 输入覆盖 EDA 正常路径。端到端 KiCad 正向案例测试位于 `reference` 分支，负极性、递归、异常域等精细语义由[手写夹具](../../tests/fixtures/README.md)覆盖。
