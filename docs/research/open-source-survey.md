# 开源参考与几何实现依据

## 参考对象

以下记录对应项目调研使用的本地检出与来源，更新参考前核对实际提交及其许可文件。

| 项目 | 调研记录中的许可 | 参考价值 |
| --- | --- | --- |
| [OdbDesign](https://github.com/nam20485/OdbDesign) | AGPL-3.0 | 文件模型、领域模型、符号文法及归档结构 |
| [KiCad](https://gitlab.com/kicad/code/kicad) | GPL-3.0 | ODB++ 导出器、源板参数与测试数据 |
| [ODB2kicad](https://github.com/FixturFab/ODB2kicad) | GPL-3.0 | 样本、坐标及 EDA 图元映射 |
| [ODBPy](https://github.com/ulikoehler/ODBPy) | Apache-2.0 | 轻量解析和组件信息组织 |
| [pcb2svg](https://github.com/vlovo/pcb2svg) | BSD-3-Clause | features / profile 到 SVG 的预览路径 |
| [mcix/odbpp](https://github.com/mcix/odbpp) | 调研对象未声明 | Java 解析结构与公开样本 |
| [ODBplusplus-Parser](https://github.com/sjgallagher2/ODBplusplus-Parser) | GPL-3.0 | v7 仿真几何结构，需区分版本 |
| [odb-pp-parser](https://github.com/capablemonkey/odb-pp-parser) | 调研对象未声明 | features / netlist 的 JSON 组织 |

本地参考存放于 `external/`，生产实现与参考检出分开。引入代码和数据时分别核对来源与适用许可。

## 可复用的设计思路

OdbDesign 的 FileModel / ProductModel 分离，适合对照“逐文件读取”与“单板实体语义”的职责。
标准符号解析使用完整文法，自定义符号通过目录引用读取；归档层屏蔽压缩与目录差异。
KiCad 源板尺寸与导出器提供独立测试输入，官方格式规范负责最终语义解释。

本项目采用标准库归档 IO、pyclipper 正向布尔与扫掠、Shapely 简单域几何检查、Pydantic IO 模型。
圆弧按物理弦高误差离散，整数几何核统一为 1e-6 mm；材料采样使用独立扫描线 / NumPy 分支。
运行依赖与模块职责见 [技术栈](../tech_stack.md)。

## 社区资料的复核点

二手文档中的坐标单位、标准符号参数单位、介质层基底和负片层合成需要逐项回到官方规范核对。
坐标在 MM / INCH 下分别为 mm / inch，标准尺寸参数分别为 µm / mil；不能采用“坐标就是整数微米”或“符号参数为 0.1 µm”的通用读法。
外部实现能显示某板，仍需对照尺寸、岛孔、单位、层域和 Z 语义验证。
参考项目结构与调研原始证据见 [归档调研](../archive/research/04-open-source-survey.md)。
