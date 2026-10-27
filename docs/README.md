# 项目文档

| 入口 | 职责 |
| --- | --- |
| [项目说明](../README.md) | 简介、产品与快速开始 |
| [需求](../requirements.md) | 数据流、物理约束、功能范围与验收 |
| [协作规则](../AGENTS.md) | AI 协作、开发与文档规范 |
| [技术决策](decisions/README.md) | 解析、逐层域、region、网格、材料与发布约定 |
| [开发说明](development.md) | 环境、CLI、契约同步、测试、基准与打包 |
| [技术栈](tech_stack.md) | 数据流、技术组件和模块职责 |
| [数据接口](interfaces/README.md) | 中间 JSON、Pydantic / schema 与 MaterialMap |
| [可视化](visualization.md) | SVG / HTML、材料掩膜、物性热图与几何诊断 |
| [格式与参考调研](research/README.md) | 官方语义、真实样本、开源结构与独立参考 |
| [数学推导](derivation/README.md) | 串并联热导率、密度与质量加权比热 |
| [参考资料](reference/README.md) | 官方 PDF、摘要、页码约定与参考 CSV |
| [验证数据](validation/README.md) | 验收方法、报告索引及证据适用范围 |
| [原始文档归档](archive/README.md) | 保留原文与基线的资料快照 |

现行文档按职责和主题维护，需求定义功能边界，技术决策说明实现约定与依据。
操作命令集中于开发说明，接口字段集中于接口文档；调研、推导和验证分别提供来源、公式与证据。
设计演变由 Git 记录，归档资料供追溯核对，当前开发使用本页指向的现行主题说明。
