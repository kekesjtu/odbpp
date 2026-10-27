# ODB++ v8.1 格式与单位

## 依据与文件模型

项目使用 Siemens *ODB++Design Format Specification* Release 8.1 Update 4，引用 `p.<PDF 页码>`，版本与摘要见 [参考资料](../reference/README.md)。
ODB++ 是目录树和文本记录组成的产品模型，tar / zip 是传输包装；文件可能按 gzip 压缩，读取应探测内容魔数。

| 路径 | 信息 |
| --- | --- |
| `misc/info` | 产品、ODB++ 版本与默认单位 |
| `matrix/matrix` | step、层身份、上下文、顺序、类型、FORM 和钻孔端点 |
| `steps/<step>/stephdr` | step 属性和重复引用声明 |
| `steps/<step>/profile` | 整板实体轮廓 |
| `steps/<step>/layers/<layer>/profile` | 层级实体域 |
| `steps/<step>/layers/<layer>/features` | 图元与符号表、属性表 |
| `steps/<step>/layers/<layer>/attrlist` | 层属性与自身单位作用域 |
| `steps/<step>/layers/<layer>/tools` | 钻孔工具尺寸与类型 |
| `symbols/<name>/features` | 自定义符号几何 |

matrix 是层目录与顺序依据，实体名需要匹配样本中的大小写差异。单板 step 是当前解析范围。

## 双轨单位

规范 p.23–26 规定文件作用域与一般度量，p.36、p.166 规定标准符号和专用尺寸。

| 数值 | MM 作用域 | INCH 作用域 | 输出 |
| --- | --- | --- | --- |
| 坐标、一般长度 | mm | inch | mm |
| 标准符号参数、resize、钻孔工具尺寸 | µm | mil | mm |
| `.layer_dielectric` | mm | inch | 派生绝对 Z：mm |
| `.copper_weight` | µm 厚度 | oz/ft² 面密度 | 按物理量推导厚度并保留来源 |

长度换算为：inch ×25.4；µm ×0.001；mil ×0.0254。
例如 MM 的 `r200` 是直径 0.2 mm，INCH 的 `r10` 是直径 0.254 mm。
字段解释必须依据本文件及符号声明的有效单位；`misc/info` 缺默认单位时采用 INCH 并警告。
matrix 的 `CU_TOP/CU_BOTTOM` 是 UID 引用，不是坐标或层名。

英制铜重按面密度除以假定铜密度 8.96 g/cm³ 派生厚度，保留 `kind=areal_density_oz_ft2`、`unit=oz/ft2`、假定密度与来源。
公制保留 `kind=thickness_um`、`unit=um`。
规范 `.layer_dielectric` 用于非铜层；非规范输入的解释需明确标识。

## 属性与记录

features 的 `$` 表声明符号，`@` 表声明属性名称，`&` 表声明属性字符串；属性通过索引间接寻址，表在顺序解析时实时可见。
分号分隔图元主体与属性区，逗号分隔属性赋值。布尔属性可以只列索引，表示 true（p.178）。
属性文件独立解析和保留单位，不能用 job 默认覆盖显式声明。

## 实现对照与证据范围

`pcbkit.textio/metadata/features/units` 分别处理文本、来源、图元和换算。
几何核使用 1e-6 mm 整数量化，最终输出分辨率由 `--digits` 明确指定；浮点比较与量化容差分开处理。
`.sum` 的 SUM 算法在采用规范中缺少完整定义；样本及其旁车保持字节稳定。
XML 扩展及拼板范围由 [需求](../../requirements.md) 定义，单板接口见 [中间 JSON](../interfaces/intermediate-json.md)。
