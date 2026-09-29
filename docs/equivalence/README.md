# docs/equivalence/ — 物性等效器文档

数据流第 4-5 段（中间 JSON → `MaterialMap.csv`）与紧随其后的可视化（掩膜 PNG、
`kx|ky|kz` 热图）的实现说明、默认参数依据、验证结果与已知近似。

| 文档 | 内容 |
|---|---|
| [01-material-equivalence.md](01-material-equivalence.md) | 输入契约、矩形化、网格、光栅、材料默认值、等效公式、钻孔规则、输出格式与诊断、合成/真实 case/收敛验证、已知近似 |
| [02-visualization.md](02-visualization.md) | 等效结果的掩膜 PNG、`kx`/`ky`/`kz` 热图与图例页：命令、语义、色标、内存、失败处理与验证 |

相关推导（数学模型来源）：

- [`../derivation/串并联PCB等效热模型推导.md`](../derivation/串并联PCB等效热模型推导.md)
- [`../derivation/密度和热容等效热模型推导.md`](../derivation/密度和热容等效热模型推导.md)

参考输出（仅用于对比「需要表达什么」，未照抄）：
[`../Flotherm_reference_MaterialMap.csv`](../Flotherm_reference_MaterialMap.csv)。
