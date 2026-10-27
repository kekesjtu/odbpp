# 数据接口

| 接口 | 权威源与用途 |
| --- | --- |
| [中间 JSON](intermediate-json.md) | Parser 与等效器之间的唯一板数据接口；同源 Pydantic 模型 |
| [解析器开发指南](parser-guide.md) | 导出责任、物理语义与完整合成示例 |
| [中间 JSON Schema](intermediate.schema) | 从 `contracts/intermediate.py` 生成的文本字段说明 |
| [MaterialMap](materialmap.md) | region 层级输出、六边界、XY 网格线和行主序五项物性 |

接口保持唯一当前结构，字段与必要物理语义在 IO 边界校验。内部图元和计算结构使用 dataclass / 数组。
Pydantic 的跨字段校验与几何算法各自履行责任；JSON Schema 供阅读和工具使用。
维护命令见 [开发说明](../development.md#契约维护)。
