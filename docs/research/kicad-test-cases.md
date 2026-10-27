# KiCad 小型正向案例

## 输入与独立预期

本地 `cases/kicad-positive/` 保留源板、原生导出 `native/`、整理版 `clean/` 以及生成 / 导出 / 整理脚本。
源板均为 20×10 mm；源设计参数和几何公式提供独立预期，普通 pytest 读取审定输入，不在运行期间调用 EDA 软件。
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

原生 ZIP 完整保留 KiCad 产物。整理脚本只移除明确的重复 / 空辅助层和无关文件并重排 ROW。
物理层 features、attrlist、tools 和 step profile 保持字节一致，额外数据触发检查失败。
重新生成时比较结构和物理语义，UUID、时间戳和记录顺序不作为整包相等要求。

## 单位与厚度

KiCad 两种单位导出均可能写 `.copper_weight=1.0`；按规范 MM 表示 1 µm，而 INCH 表示 1 oz/ft²。
因此可要求 XY / 孔径归一化一致，绝对 Z 应按各自物理量与记录来源核对。
厚度断言只使用导出文件实际提供的数据，缺失时验证 best-effort 与 false 标记。

## 维护入口与覆盖边界

```powershell
uv run --all-extras pytest tests/integration/test_kicad_positive_cases.py
```

本地案例目录中的 README 和脚本维护指定工具链的再导出流程；CLI 使用 `pcb export odb --precision 6 --compression zip`，生成后核对 misc/info 的 v8.1 声明。
KiCad 输入覆盖 EDA 正常路径，负极性、递归、异常域等精细语义仍由 [手写夹具](../../tests/fixtures/README.md) 验证。
