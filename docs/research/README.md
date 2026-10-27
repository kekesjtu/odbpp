# 格式与参考调研

调研记录可核对的规范依据、样本观察、外部实现价值与证据范围。当前功能以 [需求](../../requirements.md) 为准，实现约定见 [技术决策](../decisions/README.md)。

| 主题 | 内容 |
| --- | --- |
| [格式与单位](format-and-units.md) | 文件作用域、双轨单位、属性和铜重 |
| [几何与符号](geometry-and-symbols.md) | 图元、扫掠、极性、岛孔和符号展开 |
| [案例清单](case-inventory.md) | 四份参考输入的规模、图元、层域和覆盖范围 |
| [开源参考](open-source-survey.md) | 参考对象、可借鉴结构和来源边界 |
| [KiCad 小型案例](kicad-test-cases.md) | 本地源板、原生 / 整理版导出和独立预期 |
| [Viewer 参考](viewer-reference.md) | 本机查看器观察与黑盒核验范围 |

ODB++ 格式语义以项目采用的 Release 8.1 Update 4 为依据；PDF 来源、摘要和页码约定见 [参考资料](../reference/README.md)。
真实样本存放在本地 `cases/`，开源检出位于 `external/`，两者按需准备。合成夹具见 [夹具说明](../../tests/fixtures/README.md)。
