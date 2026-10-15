# docs/equivalence/ — 物性等效器文档

数据流第 4-5 段（中间 JSON → `MaterialMap.csv`）与紧随其后的可视化（掩膜 PNG、
`kx|ky|kz` 热图）的实现说明、默认参数依据、验证结果与已知近似。

| 文档 | 内容 |
|---|---|
| [08-parser-interface-guide.md](08-parser-interface-guide.md) | 面向 ODB++ 几何解析器开发人员：接口职责、导出约束、完整 JSON 示例与中文注释版 intermediate.py |
| [drill-policy-validation.json](drill-policy-validation.json) | 未扣孔介质 JSON、仅介质 DRILL 采样的真实案例前后对照，铜层无 DRILL 参考与几何不变检查 |
| [06-drill-rasterization.md](06-drill-rasterization.md) | 通孔/盲孔/埋孔、二维孔形、跨度判定、扫描线逻辑及 NPTH/空心孔/背钻的当前简化 |
| [05-inscribed-architecture.md](05-inscribed-architecture.md) | 当前 v5：竖条内接阶梯、独立密集光栅、全局笛卡尔栅、自适应档位、完整体积物性、内部预算与限制 |
| [output-validation.json](output-validation.json) | 19 个最终输出的逐单元全局轴线、域归属、完整体积、采样容量和物性范围核查 |
| [architecture-validation.json](architecture-validation.json) | 可重放的基线对比、真实案例几何坐标哈希、逐物性采样误差、运行时间与峰值内存 |
| [01-material-equivalence.md](01-material-equivalence.md) | 输入契约、矩形化、网格、光栅、材料默认值、等效公式、钻孔规则、输出格式与诊断、合成/真实 case/收敛验证、已知近似 |
| [02-visualization.md](02-visualization.md) | 等效结果的掩膜 PNG、`kx`/`ky`/`kz` 热图与图例页：命令、语义、色标、内存、失败处理与验证 |
| [03-case-output-sync.md](03-case-output-sync.md) | `out/` 按 case 同步 JSON/SVG/HTML、MaterialMap CSV/meta、掩膜与热图，及复用/重建规则 |
| [04-grid-sizes-and-regions.md](04-grid-sizes-and-regions.md) | 网格尺寸上下限、region 面积诊断图、刚挠/异形板分区统计与重新导出验证 |

相关推导（数学模型来源）：

- [`../derivation/串并联PCB等效热模型推导.md`](../derivation/串并联PCB等效热模型推导.md)
- [`../derivation/密度和热容等效热模型推导.md`](../derivation/密度和热容等效热模型推导.md)

参考输出（仅用于对比「需要表达什么」，未照抄）：
[`../Flotherm_reference_MaterialMap.csv`](../Flotherm_reference_MaterialMap.csv)。

当前中间格式：[07 中间 JSON 与 Pydantic 边界](07-intermediate-json.md)，
文本定义：[intermediate.schema](../intermediate.schema)。

解析审计字段移除验证：[remove-parse-info-validation.json](remove-parse-info-validation.json)。

根诊断移除验证：[remove-root-diagnostics-validation.json](remove-root-diagnostics-validation.json)。
