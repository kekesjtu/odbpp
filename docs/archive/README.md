# 原始文档归档

本目录保留本轮文档整理前的原始说明、审阅、修复、调研和推导，作为可追溯的资料快照。
原文包含各自记录时的命令、路径、字段和结论，阅读时结合对应基线；当前实现及可执行命令使用 [项目文档](../README.md)。

| 目录 | 保存内容 |
| --- | --- |
| [equivalence](equivalence/README.md) | 各阶段等效器、接口、分发与几何方案说明 |
| [reviews](reviews/README.md) | 项目问题、性能及显示交互审阅 |
| [fixes](fixes/README.md) | 对应根因、修改与验证记录 |
| [research](research/README.md) | 原始规范、样本、开源和工具调研 |
| [derivation](derivation/README.md) | 两份原始模型推导入口 |

验证方法见 [validation](../validation/README.md)。历史验证报告在当前工作区的 `tmp/validation/history/`，也可从 Git 提交 `2e60504` 的 `docs/validation/reports/` 恢复；新环境无需依赖这些本地文件。
官方文件与参考 CSV 位于 [reference](../reference/README.md)。逐文件来源与存放位置见 [归档清单](manifest.json)，清单记录 Git 基线摘要和存放文件的 SHA-256。
原始文档的相对资料链接按归档位置调整，行尾与空白统一规范；引用中记录的历史代码和命令保持其证据语义。
日常维护更新现行主题文档，设计演变由 Git 记录。
