# 项目结构与文档入口

`pcbkit-rust` 负责 ODB++ 单板解析，`pcbeq-rust` 负责 PCB 物性等效，两者独立分发，通过中间 JSON 交接。产品边界、术语和数据流见[项目架构](architecture.md)，功能范围与验收条件见[需求](requirements.md)。环境、构建和运行命令见[根 README](../README.md)。

## 项目结构

| 路径 | 职责 | 阅读入口 |
| --- | --- | --- |
| `packages/pcbkit-rust/` | ODB++ 解析器、Rust 扩展、Python 输入适配与工程预览 | [解析器使用说明](../packages/pcbkit-rust/README.md) |
| `packages/pcbeq-rust/` | 独立 Rust 等效计算 CLI，以及可选的 Python 管线和绘图 | [等效器使用说明](../packages/pcbeq-rust/README.md) |
| `contracts/` | 中间 JSON 的共享 Pydantic 模型与生成 schema | [中间 JSON 契约](interfaces/intermediate-json.md) |
| `tests/` | 接口、集成、分发验证与最小手写夹具 | [测试目录](../tests/) |
| `tools/` | 契约同步、进程管道、案例输出、审计、基准及分发验证工具 | [维护工具](../tools/README.md) |
| `docs/` | 需求、架构、设计决策、接口、领域调研及参考资料 | 本页下方的专题索引 |
| `cases/` | 本地 ODB++ 案例与来源资料 | [案例目录](../cases/) |
| `out/` | 按案例保存的解析、等效、图像和管道正式产物 | [输出与发布](decisions/output-publication.md) |
| `.scratch/` | 任务专属的临时脚本、调试文件与实验产物 | [协作规范](../AGENTS.md) |
| `tmp/` | 程序管理的临时文件与运行日志 | [协作规范](../AGENTS.md) |

Python 环境与依赖由根目录的 `.python-version`、`pyproject.toml` 和 `uv.lock` 管理；Rust workspace 与依赖由 `Cargo.toml` 和 `Cargo.lock` 管理。开发约定见 [AGENTS.md](../AGENTS.md)。

## 需求与架构

- [需求与验收](requirements.md)：功能范围、物理语义和输出约束。
- [项目架构](architecture.md)：产品边界、术语、数据流及语言职责。
- [输出与发布](decisions/output-publication.md)：数值、图像和案例产物的发布边界。

## 接口契约

- [中间 JSON](interfaces/intermediate-json.md)：解析器与等效器之间的板数据接口。
- [MaterialMap](interfaces/materialmap.md)：完整单元的几何和等效物性输出。
- [pcbeq 诊断格式](interfaces/pcbeq-diagnostics.md)：运行诊断、告警、错误及统计。

## 解析器设计

- [解析语义](decisions/pcbkit/parser-semantics.md)：几何解释、极性、精度及输入边界。
- [逐层轮廓与叠层](decisions/pcbkit/layer-profiles-and-stackup.md)：层实体范围、厚度与 Z 信息。
- [查看器交互](decisions/pcbkit/viewer-interaction.md)：工程预览和层序矩阵交互。

## 等效器设计

- [轮廓近似 region 与局部网格](decisions/pcbeq/geometry-planning.md)：区域划分、误差和拓扑约束。
- [材料采样](decisions/pcbeq/material-sampling.md)：光栅采样、网格归属及材料覆盖。
- [材料模型](decisions/pcbeq/material-model.md)：两相物性与等效计算规则。
- [可视化](decisions/pcbeq/pcbeq-visualization.md)：从正式结果恢复工程图表。

## 领域调研

研究资料说明外部格式、领域知识和参考工具，不描述项目实现。ODB++ 格式专题依据 Release 8.1 Update 4，各篇附原文页码与示例。

| 主题 | 文档 |
| --- | --- |
| 产品模型、目录与文件依赖 | [ODB++ 文件树](research/odbpp_format/file-tree.md) |
| 整板与逐层外形、孔洞及缺省关系 | [Step profile 与 layer profile](research/odbpp_format/profiles.md) |
| 默认单位、文件单位与局部覆盖 | [单位体系与作用域](research/odbpp_format/units.md) |
| 铜重、铜厚、介质和其他材料厚度 | [层厚度](research/odbpp_format/layer-thickness.md) |
| `$`、`@`、`&` 与属性索引 | [声明与属性](research/odbpp_format/declarations-and-attributes.md) |
| 图元、标准及自定义符号、变换与极性 | [图元与符号](research/odbpp_format/geometry-and-symbols.md) |
| 外部查看工具 | [Siemens ODB++ Viewer](research/odbpp_viewer/viewer-reference.md) |

## 推导与参考来源

- [官方规范与参考格式](reference/format-sources.md)：ODB++ 规范版次、来源及其他参考格式。
- [2DVTN 二维热阻网络 PCB 等效热模型推导](derivation/二维热阻网络PCB等效热模型推导.md)。
- [SPTRA 串并联 PCB 等效热模型推导](derivation/串并联PCB等效热模型推导.md)。
- [密度和热容等效热模型推导](derivation/密度和热容等效热模型推导.md)。
