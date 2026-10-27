# 参考案例与覆盖范围

本表描述四份固定本地参考输入；小型 EDA 案例另见 [KiCad 案例集](kicad-test-cases.md)。
真实样本位于 `cases/`，来源与原始字节独立保存，手写夹具位于 `tests/fixtures/`。

## 规模与来源

| 输入 | 来源记录 | 单位 | 铜 / 介质 / 钻孔层 | matrix 总层数 | 自定义符号 |
| --- | --- | --- | --- | ---: | ---: |
| minimal-test-odb | ODB++ Test Generator，mcix/odbpp 样本 | MM | 2 / 0 / 0 | 2 | 0 |
| kicad-kitchen-sink | FixturFab/ODB2kicad 样本，KiCad EDA | MM | 2 / 1 / 1 | 37 | 0 |
| kicad-video-odb.tgz | mcix/odbpp 样本，KiCad EDA 9.0.3 | MM | 4 / 3 / 1 | 28 | 0 |
| designodb-rigidflex.tgz | ODB++ 官方样本，Xpedition / Valor | INCH | 10 / 9 / 7 | 43 | 123 |

四份输入声明 ODB++ 8.1 且各含一个 step。原始普查包含层与 symbols 中的 features / profile：

| 输入 | P | L | A | S | OB 孔环 |
| --- | ---: | ---: | ---: | ---: | ---: |
| minimal-test-odb | 21 | 6 | 2 | 1 | 0 |
| kicad-kitchen-sink | 55 | 358 | 2 | 4 | 3 |
| kicad-video-odb | 13,946 | 29,720 | 21 | 21 | 0 |
| designodb-rigidflex | 41,550 | 471,158 | 958 | 5,274 | 987 |

这些计数的范围是固定参考输入，不代表每个后续新增本地案例。

## 对解析与建模有用的观察

- minimal 的一条负极性弧提供最小保序差集样例，双铜层用于基础导出。
- kitchen-sink 的介质为 `F 0`，没有层级 profile，使用 step 基底；matrix 的大写名称与小写目录测试实体名匹配。
- KiCad 过孔盖帽、填充、塞孔等辅助目录可能未登记于 matrix，层清单以 matrix 为依据。
- video 的通孔从 TOP_COPPER 到 B.CU，四铜 / 三介质结构适合检查跨度和缺厚度状态。
- 刚挠板有 23 份层级 profile，覆盖全部九介质层；层域与整板域不同，部分域还越出 step。
- 刚挠板的通孔、盲孔、埋孔及 FLEX_5 → FLEX_6 钻孔验证跨层映射；同一 DRILL 层可混合 PLATED / NON_PLATED 工具。
- INCH 坐标与 mil 符号 / 孔径在刚挠板中并存，例如 `r10.827` 对应约 0.275 mm。
- 四例共 110 个 LAYER 块都省略 FORM；FLEX 铜层可由 PG_FLEX 子类型给出，来源标记仍需保留。

## 符号与覆盖边界

刚挠板有 499 条符号声明，其中 490 个标准符号引用、9 个自定义符号引用；123 个自定义目录中有 114 个未被层文件引用。
被引用定义包含 `sc_join0201_hd`、`special_0.670x0.950_nt2520` 等，内部仅用标准符号，适合验证严格命名和惰性读取。

四例中有负极性记录、真实孔洞及自定义圆弧，matrix 层极性均为 POSITIVE。
负片层、递归嵌套、循环引用、resize、失败层级 profile 及显式 FORM 由最小合成夹具和包内测试补充。
原始完整普查作为输入证据保存在 [归档记录](../archive/research/03-case-inventory.md)。
