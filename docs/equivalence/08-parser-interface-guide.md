# 中间 JSON：ODB++ 几何解析器开发指南

本文面向负责 ODB++ 文本读取、符号展开、图元极性及几何规整的开发人员。目标是明确解析器需要交付什么数据、为何需要这些字段，以及如何通过当前 `intermediate.py` 输出可被等效器正确理解的实体模型。

源码依据：[`src/odbpp/intermediate.py`](../../src/odbpp/intermediate.py)。本指南对应移除 `parse_info` 后的当前实现；附录包含完整源码，仅增加中文注释，不改变任何可执行语句。

## 1. 接口在流水线中的作用

```text
ODB++ 文件、单位作用域、符号定义、图元正负极性
                    │
                    ▼
Parser：单位换算 → 符号展开 → 极性合成 → 拓扑与坐标规整
                    │
                    ▼
内部 dataclass / Ring / Region
                    │ 导出边界构造并校验 BoardDocument
                    ▼
                中间 JSON
                    │ 导入边界通过同一 Pydantic 模型校验
                    ▼
    ┌───────────────┴────────────────┐
    ▼                                ▼
step profile + 逐层计算域        铜图案 / DRILL 投影
    │                                │
region 与物性网格规划           独立材料光栅采样
    └───────────────┬────────────────┘
                    ▼
           网格统计与 MaterialMap
```

中间 JSON 是 Parser 与等效器之间唯一的数据接口。它交付规整后的几何及其必要物理语义，使下游无需读取 ODB++ 文件、访问符号库或重新解释原始图元极性。

解析器交付多边形、计算域、层身份、绝对 Z 和钻孔跨度。region 的内接近似、全局网格划分、材料参数、光栅分辨率和物性公式由等效器负责，不写进这个接口。SVG/HTML 是并行的工程核对输出，等效器不从 SVG 路径或截图恢复几何。

`parse_info`、原始层极性、图元数量和极性段计数已移出中间 JSON；这些信息仍可存在于解析器内部统计中。根节点不导出 `diagnostics`，诊断保留在内部模型、CLI 或独立报告中。`geometry_status`、厚度可信状态及来源保留，因为它们决定下游能否把某个几何结果视为可用实体。

## 2. Pydantic 模型与 .schema 各负责什么

| 对象 | 当前职责 | 解析器开发人员如何使用 |
|---|---|---|
| `intermediate.py` 中的模型 | 定义字段并执行类型、范围与跨字段关系校验 | 在导出边界构造 `BoardDocument`；不要为每个原始图元构造模型 |
| `docs/intermediate.schema` | 从模型生成的 JSON Schema 文本，供接口阅读及外部工具使用 | 查字段形状；随模型变更重新生成，不手工维护另一套定义 |
| `tests/test_intermediate.py` | 检查导入约束、round-trip 和 schema 与模型的一致性 | 修改接口后执行相应测试 |
| 几何算法 | 单位转换、极性合成、拓扑、有效性和坐标量化 | 在输出接口模型之前完成；Pydantic 不执行这些算法 |

生产链路直接使用 Pydantic，没有从 `.schema` 文件加载另一套运行时校验器。Python 自定义校验器中的域引用、层跨度等关系也不会全部表达在生成的 JSON Schema 中。

项目只有一套当前格式，不输出或协商 `schema` / `generator` 业务版本，不维护旧字段兼容迁移。Schema 文本中的 `$schema` 声明的是 JSON Schema 的标准语法。`source.product.odb_version` 记录输入 ODB++ 格式来源，也不是中间格式版本。

## 3. 必须区分三种几何

| 位置 | 几何含义 | 典型用途 |
|---|---|---|
| `profile_domain_id` 指向的 domain | 原始整板 step profile | 定义整板范围、全局光栅范围和几何规划基础 |
| 某物理层 `domain_id` 指向的 domain | 该层实际存在的二维计算域 | 决定该层在哪里可以生成完整 region / 三维网格 |
| 某层 `polygons` | 本层图元经规整得到的正向实体图案 | 铜层提供铜图案；介质提供未扣 DRILL 的基底几何；DRILL 提供跨层投影 |

例如，在 100 mm × 50 mm 的铜层计算域内，铜走线可能只覆盖 10% 面积。这不会缩小铜层的计算域；域内没有铜图案的位置仍是该层的基底材料。用铜走线包围盒或铜图案代替 domain，会让白色基底区域从模型中错误消失。

刚挠板中，某介质层可能只存在于左侧刚性区。这个事实必须由该层自己的 `domain_id` 表达，不能让等效器先生成整板网格，再根据铜覆盖率或少量像素猜测该层是否存在。

domain 的选择流程固定为：有效层级 profile → 缺失时取 step profile。存在但解析失败的层级 profile 必须报告失败，不能回退成 step profile。不同层可以引用同一个 domain；引用是一份几何的标识，不意味着下游需要重新选择 profile。

## 4. Parser 在导出前必须完成的工作

1. 选择单板实体 step；当前不展开拼板矩阵重复或嵌套 step 引用。
2. 分别解释坐标单位与标准符号、resize、钻孔尺寸的专用单位规则，统一得到毫米几何。不能只给全部数字乘一个统一单位因子。
3. 展开支持的标准符号及递归自定义符号，把旋转、镜像、平移和线/弧扫掠落实到实体几何。
4. 按输入顺序和极性语义完成正负图元合成，消除同向岛的未规整重叠，明确保留岛、孔和孔内嵌套岛。
5. 对整板与逐层 profile 采用同样明确的单位及拓扑处理，确定 domain 引用。
6. 在实际写出的坐标分辨率上量化并重新规整几何，再检查退化环。不能只在序列化时截断小数，而继续相信截断前的几何。
7. 根据层叠与厚度来源输出绝对 Z，解析铜层索引和必要的介质 / DRILL 铜层跨度。
8. 构造完整层目录，把空图案、解析失败与未导出状态区分开，再通过接口模型校验并写文件。

已有实现中的导出入口是 `export.py::step_to_document()`，profile 域构建为 `export.py::_domains_json()`，厚度及跨度解析为 `stackup.py`。这些函数负责把内部解析结果映射到本文接口，不属于附录中的 Pydantic 几何算法。

### 多边形约定

所有 `points` 使用输入 step 的原点，XY 中 Y 向上，单位 mm。坐标对形如 `[x, y]`；末尾不重复首点，消费者自动闭合边界。规整输出的岛顺时针、孔逆时针，同时必须显式给出 `island`：`true` 表示实体岛，`false` 表示孔。

环表示正向实体的边界集合。孔是实体的拓扑空洞，不能把它理解为需要按绘制顺序重新执行的 ODB++ 负图元；孔内岛仍写成 `island=true` 的独立环。消费者不需要知道原始 features 是怎样一笔一笔画出来的。

### 三个不同的尺度

`resolution_mm` 是最终坐标量化间距，默认 0.0001 mm。弧离散的物理弦高容差控制曲线近似；`Px/Py` 控制下游材料采样。三者分别控制不同误差。改变光栅分辨率不得改写 JSON 几何、domain、region 或物性网格坐标。

## 5. 当前字段的交付要求

以下表格同时说明结构允许范围和实际 Parser 的交付责任。模型允许某字段缺省，通常是为了表达合成数据、非实体层或失败状态；这不意味着正式的待等效物理层可以丢失必要语义。

### 根与 step

| 字段 | 含义与交付要求 |
|---|---|
| `units` | 必须是 `mm`；不能仅修改这个标签而不转换数字 |
| `resolution_mm` | 实际最终坐标规整间距，有限且大于零 |
| `source` | 可选来源记录；现有真实 Parser 导出包含它；手工合成数据可以省略 |
| `source.path / job_root` | 输入位置与 job root，供追溯，不决定几何原点 |
| `source.input_units` | 输入记录的 `MM` 或 `INCH`；不能用它重新换算已经为 mm 的几何 |
| `source.product.name / odb_version` | 产品名称可缺省，输入 ODB++ 版本必填于 product 中 |
| `steps` | 恰好一个实体 step；step 内 `name` 非空 |
| `profile_domain_id` | 整板轮廓引用；可为空以表达轮廓缺失，但这种数据不能用于正式整板等效 |
| `domains` | `{id, source, polygons}`；id 唯一；source 为 `step_profile` 或 `layer_profile` |
| `layers` | 完整层目录，按 matrix row 排列；名称唯一，保留非物理、失败与未导出层 |

domain id 是稳定引用字符串；当前导出器用 `step_profile`、`layer_profile:<层名>`，但模型不硬编码 id 名称。`profile_domain_id` 指向的条目必须有 `source=step_profile`。

### layer

| 字段 | 含义与交付要求 |
|---|---|
| `name / type / context / row` | 原层身份、ODB++ 类型、BOARD/MISC 上下文和正整数 matrix row；供下游识别物理层与报告跳过原因 |
| `form / form_source` | RIGID/FLEX 及其 DECLARED/FLEX_SUBTYPE/DEFAULT 来源；结构允许缺省为 RIGID/DEFAULT |
| `geometry_status` | `ready`、`failed` 或 `omitted`，正式输出应显式记录 |
| `geometry_error` | 失败 / 域不可用的具体原因；`failed` 状态必须有非空原因 |
| `domain_id` | 对已选定逐层计算域的引用；待等效物理层必须有有效、非空的计算域 |
| `polygons` | 必填列表，可为空；无需额外 area、bounds 或图元极性缓存 |
| `z_mm` | 绝对 Z 起止、联合可信状态和来源；板内实体物理层必须交付，DRILL 不导出 |
| `thickness` | 本层厚度可信状态及来源，可保留原始物理量；数值建模厚度从 Z 差计算 |
| `copper_index` | BOARD 铜层自上而下的非负整数索引；层目录中不得重复 |
| `copper_span` | 介质 / DRILL 的两个递增铜层索引，端点必须存在于同一层目录 |

类型字符串不被收窄成仅铜 / 介质枚举：DOCUMENT、COMPONENT、MASK 等输入仍应有明确层身份，由等效器报告是否跳过。将不支持的层改成 DIELECTRIC 会错误套用 FR4 模型。

`ready + polygons=[]` 是已成功解析的空图案，例如一层没有铜图案；它可以仍有完整计算域。`failed + polygons=[]` 表示计算不可用，必须携带错误。`omitted + polygons=[]` 表示筛选等原因未交付几何。不能删除失败的物理层来制造一个看起来完整的接口；根诊断列表不进入文件，失败状态仍须保留。

### Z、厚度与原始量

Z 从叠层顶面向下，使用绝对 mm 坐标；`end_mm >= start_mm >= 0`。未知厚度使用 best-effort 位置，可以出现零厚度区间，必须明确 `thickness_known=false`，不能伪造正厚度以通过结构校验。

`thickness.known` 只表示本层厚度是否有可靠来源；`z_mm.thickness_known` 表示本层以及累计绝对位置的联合可信状态。上方层厚度缺失时，本层厚度已知也不能保证绝对位置可信。这是两个不同问题，不应合并布尔字段。

| 原始来源 | `kind` | `unit` | 处理 |
|---|---|---|---|
| 非铜材料 `.layer_dielectric` | `thickness` | `mm` 或 `inch` | Parser 换算后形成绝对 Z，原始值只作来源追溯 |
| MM 文件中的 `.copper_weight` | `thickness_um` | `um` | µm 铜厚度，换算后参与叠层 |
| INCH 文件中的 `.copper_weight` | `areal_density_oz_ft2` | `oz/ft2` | 面密度，按 8.96 g/cm³ 的默认铜密度换算厚度 |

英制铜重记录必须带 `thickness_source=assumed_density`、`assumed_density_g_cm3=8.96`，不能将 oz/ft² 误读成长度。`input_value/kind/unit` 是明确的原始量例外，等效器不会再据此换算或覆盖 Z。`spec_deviation` 记录偏离规范的厚度属性来源；具体解释由 Parser / stackup 完成。

## 6. DRILL 与介质：导出者最容易混淆的边界

中间 JSON 的介质 `polygons` 保留未扣除跨层 DRILL 的几何。DRILL 作为独立层保留正向投影和 `copper_span`，不保存可推导的 `z_mm`；层身份和解析状态仍用于识别。需要绝对范围时，由起点铜层 `z_mm.start_mm` 与终点铜层 `z_mm.end_mm` 推导，它不产生一个额外厚度层。铜层图案在 JSON、预览及等效器中都不受 DRILL 改写。

SVG/HTML 只在绘图副本中对介质做扣孔；已扣孔副本不能交给 `step_to_document()`。等效器对介质材料采样从域内空铜图案开始，把相交 DRILL 投影视为实心铜。材料掩膜规则是：

```text
有效域       = step profile AND 逐层计算域
铜层黑色     = 有效域 AND 铜图案
介质层黑色   = 有效域 AND 相交 DRILL 投影
域内白色     = 有效域 AND NOT 黑色
域外         = 没有该物理层实体
```

profile 的真实孔洞 / 挖空始终保留在 domain 中。DRILL 与真实孔洞重合也不能填出实体。不要为了显示介质孔洞而扣除 domain，也不要为了实心铜材料模型而填补铜图案的避空区。

例如铜层 C0、C1、C2 自上而下排列，C0→C1 的盲孔只作用于 `[0,1]` 间的介质，不影响 `[1,2]` 间的介质，也不改变任何铜层图案。跨度匹配采用严格正长度相交：`max(介质起点, 钻孔起点) < min(介质终点, 钻孔终点)`。

未知厚度可能让几个绝对 Z 重合，因此不要丢弃拓扑跨度再根据估算 Z 推断通孔。只有铜层的输入无需解析 DRILL 跨度；需要介质采样时，缺少已解算跨度或端点铜层 Z 的 DRILL 会阻止正式等效输出。

## 7. 校验通过代表什么

| 检查 | Pydantic 当前实现 | Parser / 几何或等效算法还要负责 |
|---|---|---|
| 数值、坐标形状 | 有限数值、二维坐标对、至少三个点、额外字段拒绝 | 单位已正确转换，点不重复闭合、非退化、坐标精度一致 |
| 岛 / 孔 | 要求显式布尔 `island` | 真实拓扑、方向、嵌套关系、规整与无错误自交 |
| 标识 / 引用 | 域 id、层名称、铜层索引唯一；域及跨度引用有效 | profile 真实有效、域优先规则、完整目录与正确 row 排序 |
| Z | 非负且起止有序，允许未知零厚度 | 层叠次序、必要 Z 存在、来源可信状态、铜重实际换算 |
| 厚度来源 | kind/unit 配对和英制假定密度信息 | 从正确 ODB++ 文件作用域及属性得到原始值 |
| 层状态 | 非 ready 不携带图案，failed 必须有错误 | 不把失败伪装成空图案、不隐藏未导出的物理层 |
| 可用于等效 | 模型可以表达缺域、缺 Z 或失败状态 | 等效器拒绝必要信息缺失；几何分支验证内接与拓扑 |

因此，“构造 `BoardDocument` 成功”表示接口结构和代码中声明的关系成立，不等于“PCB 已经几何有效并可完成物性等效”。例如三个重复点仍可能通过点数约束，空 domains 仍可能构造模型；Parser 应在几何处理阶段识别这些问题，并用真实状态报告。

`frozen=True` 禁止直接重新赋值模型属性，并非递归冻结 list。不要修改已验证模型中的坐标列表，也不要用 `model_construct()` 接收未验证的外部数据。`validate_document()` 对现成 `BoardDocument` 直接返回，调用方必须保持已验证对象的可信约束。

## 8. 完整的合成接口例子

这是 10 mm × 6 mm 矩形板、两铜层及一介质层的完整合成数据。下铜图案为空，但下铜层依然有计算域。DRILL 位于板内，跨越两端铜层之间的介质；介质几何仍是一整片矩形。

示例使用模型允许的缺省 RIGID/DEFAULT 等元数据，省略可选 source；正式 Parser 应写入实际来源和显式层状态。下面 JSON 可直接经当前模型导入，数值仅用于展示接口结构。

```json
{
  "units": "mm",
  "resolution_mm": 0.0001,
  "steps": [
    {
      "name": "pcb",
      "profile_domain_id": "board",
      "domains": [
        {
          "id": "board",
          "source": "step_profile",
          "polygons": [
            {
              "island": true,
              "points": [
                [
                  0,
                  0
                ],
                [
                  0,
                  6
                ],
                [
                  10,
                  6
                ],
                [
                  10,
                  0
                ]
              ]
            }
          ]
        }
      ],
      "layers": [
        {
          "name": "C0",
          "type": "SIGNAL",
          "context": "BOARD",
          "row": 1,
          "geometry_status": "ready",
          "domain_id": "board",
          "polygons": [
            {
              "island": true,
              "points": [
                [
                  2,
                  2
                ],
                [
                  2,
                  4
                ],
                [
                  4,
                  4
                ],
                [
                  4,
                  2
                ]
              ]
            }
          ],
          "z_mm": {
            "start_mm": 0,
            "end_mm": 0.035,
            "thickness_known": true,
            "source": "synthetic_stackup"
          },
          "copper_index": 0,
          "thickness": {
            "known": true,
            "source": ".copper_weight",
            "input_value": 35,
            "kind": "thickness_um",
            "unit": "um",
            "thickness_source": "attribute"
          }
        },
        {
          "name": "D0",
          "type": "DIELECTRIC",
          "context": "BOARD",
          "row": 2,
          "geometry_status": "ready",
          "domain_id": "board",
          "polygons": [
            {
              "island": true,
              "points": [
                [
                  0,
                  0
                ],
                [
                  0,
                  6
                ],
                [
                  10,
                  6
                ],
                [
                  10,
                  0
                ]
              ]
            }
          ],
          "z_mm": {
            "start_mm": 0.035,
            "end_mm": 1.535,
            "thickness_known": true,
            "source": "synthetic_stackup"
          },
          "copper_span": [
            0,
            1
          ],
          "thickness": {
            "known": true,
            "source": ".layer_dielectric",
            "input_value": 1.5,
            "kind": "thickness",
            "unit": "mm",
            "thickness_source": "attribute"
          }
        },
        {
          "name": "C1",
          "type": "SIGNAL",
          "context": "BOARD",
          "row": 3,
          "geometry_status": "ready",
          "domain_id": "board",
          "polygons": [],
          "z_mm": {
            "start_mm": 1.535,
            "end_mm": 1.57,
            "thickness_known": true,
            "source": "synthetic_stackup"
          },
          "copper_index": 1,
          "thickness": {
            "known": true,
            "source": ".copper_weight",
            "input_value": 35,
            "kind": "thickness_um",
            "unit": "um",
            "thickness_source": "attribute"
          }
        },
        {
          "name": "D_C0_C1",
          "type": "DRILL",
          "context": "BOARD",
          "row": 4,
          "geometry_status": "ready",
          "domain_id": "board",
          "polygons": [
            {
              "island": true,
              "points": [
                [
                  2.5,
                  2.5
                ],
                [
                  2.5,
                  3.5
                ],
                [
                  3.5,
                  3.5
                ],
                [
                  3.5,
                  2.5
                ]
              ]
            }
          ],
          "copper_span": [
            0,
            1
          ]
        }
      ]
    }
  ]
}
```

## 9. 在解析器中接入

### 使用本项目已有 Parser

```python
from pathlib import Path
from odbpp import load_job, build_step_preview
from odbpp.export import step_to_document
from odbpp.intermediate import read_document, write_document

Path("build").mkdir(exist_ok=True)
job = load_job("cases/kicad-kitchen-sink")
preview = build_step_preview(job)  # 内部继续使用 dataclass / Region
document = step_to_document(job, preview)  # 边界映射与校验，包含完整层目录
write_document(document, "build/pcb.json")
restored = read_document("build/pcb.json")  # 相同模型读取与验证
```

### 接入独立实现的 ODB++ 几何解析器

先完成本文第 4 节的几何处理和叠层解算，再构造与上例同形状的普通 dict。边界调用形式为：

```python
from pathlib import Path
from odbpp.intermediate import BoardDocument, write_document

# payload 是你的解析器构造的完整当前格式 dict。
# 大文件可按层构造 LayerModel，避免未验证坐标树与模型坐标树同时长期驻留。
document = BoardDocument.model_validate(payload)
Path("build").mkdir(exist_ok=True)
write_document(document, "build/pcb.json")
```

`write_document()` 接受已验证模型，不主动创建目录，也不重新解释图元或转换单位。调用方负责创建输出目录与处理写入异常。收到 `ValidationError` 应保留其字段路径和错误原因；不要捕获后填默认矩形或空图案继续输出一个伪成功结果。

`internal_document()` 是等效器进入内部算法的轻量映射。它复用坐标列表，只保留运算所需字段；来源、厚度追溯、form 等不会全部进入这个映射。文件导出应使用 `write_document()`，不应把 `internal_document()` 当作完整文件序列化 API。

## 10. 修改接口与交付检查

修改模型后，同步导出映射与调用方，并从同一模型生成 schema。文本说明和代码附录也需要同步；附录是带注释的当前实现副本，不是另一套可维护的接口定义。

```powershell
uv sync --all-extras
uv run --all-extras python -c "from odbpp.intermediate import write_schema; write_schema('docs/intermediate.schema')"
uv run --all-extras python -m pytest tests/test_intermediate.py tests/test_export.py tests/test_kicad_positive_cases.py tests/test_drill_policy.py
# 接口或几何输出发生变化时，再重导出真实案例并核查等效结果：
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out
```

交付前逐项确认：毫米数字与原点一致；岛 / 孔已规整；每个物理层有独立可引用计算域；介质 JSON 未扣 DRILL；铜层未被 DRILL 改写；真实 profile 孔洞保留；Z 与厚度来源明确；必要跨度已解算；完整层目录及失败原因未隐藏。

相关阅读：[当前接口字段参考](07-intermediate-json.md)、[DRILL 采样规则](06-drill-rasterization.md)、[中间 JSON schema](../intermediate.schema)。

## 附录：完整 intermediate.py（中文注释版）

下面保留源码的全部导入、类型、类、字段、自定义校验器及 IO 函数。只添加 `#` 中文注释；业务字段和执行逻辑与当前源码一致。

```python
"""The single current JSON contract; Pydantic is used only at IO boundaries.

Geometry kernels, feature records, rasters and equivalent cells remain their
existing dataclasses/arrays. No format versions or compatibility migrations.
"""
# 延迟求值类型注解；下面的联合类型和模型引用用于 IO 契约，不创建计算图元。
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Mapping, Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, model_validator

# Number 是数值类型约束：拒绝 NaN / ±Infinity；单位仍由 Parser 保证为 mm。
# 它不执行 ODB++ 单位换算，也不判断坐标是否位于板内。
Number = FiniteFloat
# A compact coordinate pair, not a separate Pydantic object per vertex. Both
# Python pairs and JSON arrays are accepted; strict finite elements remain.
# 坐标对采用普通 tuple 而非每个顶点一个 BaseModel，以减少大板对象开销。
# strict=False 仅放在二元容器上，允许 Python list / tuple 表示坐标对；
# 元素的数值约束仍由 Number 与整个模型的严格配置控制。
Point = Annotated[tuple[Number, Number], Field(strict=False)]
# 铜层索引为非负严格整数；不能以浮点或 bool 冒充层索引。
Index = Annotated[StrictInt, Field(ge=0)]


# 所有接口模型共用同一配置。这里只约束接口对象，不替换阶段内 dataclass。
class ContractModel(BaseModel):
    # extra="forbid"：拼错或已删除的字段会报错，例如旧 parse_info。
    # strict=True：避免把字符串、布尔值等默默转成所需物理数据。
    # allow_inf_nan=False：拒绝非有限浮点数。
    # frozen=True：禁止属性重新赋值；嵌套 list 仍可变，调用方必须避免原地修改。
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


# 一个规整后的边界环；多个环共同表达实体岛、孔及孔内岛。
# 这里不做布尔合成、自交检查、绕向校验或首尾点去重。
class PolygonModel(ContractModel):
    """A regularised positive boundary; island=False explicitly denotes a hole."""
    # true 表示正向实体岛，false 表示孔；不是待执行的 ODB++ 图元极性。
    island: bool
    # 至少三组有限二维坐标。点数约束不保证面积非零，Parser 仍需检查退化。
    # 首点不在末尾重复是导出约定，此处没有实现首尾相等检查。
    points: list[Point] = Field(min_length=3, description="毫米坐标；首点不在末尾重复；至少三个点。")


# 存储可复用计算域。物理层通过 domain_id 引用它，整板通过 profile_domain_id 引用。
class DomainModel(ContractModel):
    # id 的唯一性由 StepModel 检查；不能用铜图案的范围来构造这个域。
    id: str = Field(min_length=1)
    # 记录选定域的来源。有效层级 profile 优先；缺失时取 step profile。
    # 本字段不会触发自动回退，选域和失败报告属于 Parser。
    source: Literal["step_profile", "layer_profile"]
    # domain 的正向轮廓可包含多个岛和真实孔洞。结构允许空列表；等效仍需有效域。
    polygons: list[PolygonModel]


# 实体物理层的绝对 Z 范围；单位 mm，自叠层顶面向下。DRILL 不保存重复 Z。
class ZExtentModel(ContractModel):
    # 绝对坐标允许为零。不要把每层都重置成从零开始的局部坐标。
    start_mm: Number = Field(ge=0)
    # 实际建模厚度为 end_mm - start_mm；未知层厚允许 best-effort 零厚度。
    end_mm: Number = Field(ge=0)
    # 这是本层厚度与累计绝对位置的联合可信状态；有上方未知层时也可能为 false。
    thickness_known: bool = Field(description="本层厚度与累计绝对 Z 联合可信；未知时仍输出 best-effort Z。")
    # 来源文字由 stackup 交付；缺省 unknown 不表示已经查明跨度。
    source: str = Field(default="unknown", min_length=1, description="实体物理层绝对 Z 的来源。")

    @model_validator(mode="after")
    # mode="after" 表示字段类型校验完成后再检查起止关系；返回同一模型。
    # 这里只保证有序，不保证与邻层叠层关系正确或厚度来源可靠。
    def ordered(self):
        if self.end_mm < self.start_mm:
            raise ValueError("end_mm must be >= start_mm")
        return self


# 本层厚度来源与原始量追溯。不要把输入原始量当作第二份归一化厚度。
class ThicknessModel(ContractModel):
    # 仅本层厚度是否已知；与 ZExtentModel.thickness_known 的联合状态不同。
    known: bool = Field(description="仅本层厚度是否有有效来源，不受上方未知厚度影响。")
    # 例如 .layer_dielectric、.copper_weight 或 unknown；不是材料型号。
    source: str = Field(min_length=1)
    # Raw provenance is deliberately NOT a second normalised thickness value.
    # 输入原始值：这是 mm 几何之外的显式追溯量，解释必须结合 kind / unit。
    input_value: Number | None = None
    # 区分一般厚度、公制铜厚 µm 和英制铜重面密度。
    kind: Literal["thickness", "thickness_um", "areal_density_oz_ft2"] | None = None
    # 仅标记原始量单位；几何 points 与 z_mm 仍统一 mm。
    unit: Literal["mm", "inch", "um", "oz/ft2"] | None = None
    # 直接属性厚度或使用假定铜密度推得的厚度来源。
    thickness_source: Literal["attribute", "assumed_density"] | None = None
    # 英制铜重换算时显式记录密度，项目 Parser 使用 8.96 g/cm³。
    # 模型这里只约束正数，不在这里执行面密度到厚度的换算或强制具体值。
    assumed_density_g_cm3: Number | None = Field(default=None, gt=0)
    # 用于记录 Parser 接受的偏离规范属性来源；不自动修正输入。
    spec_deviation: bool = False

    @model_validator(mode="after")
    # 检查来源标签是否互相匹配；不计算厚度，也不改写已有 Z。
    # 没有声明 kind 的未知厚度来源可保留；声明 kind 后必须有 input_value。
    def provenance(self):
        # 英制铜重需要面密度单位、假定密度来源及实际采用的密度。
        if self.kind == "areal_density_oz_ft2":
            if self.unit != "oz/ft2" or self.thickness_source != "assumed_density" or self.assumed_density_g_cm3 is None:
                raise ValueError("imperial copper weight requires oz/ft2, assumed_density and assumed_density_g_cm3")
        # MM 作用域下铜重的专用单位是 µm，不能沿用一般坐标单位。
        if self.kind == "thickness_um" and self.unit != "um":
            raise ValueError("metric copper weight requires unit um")
        # 一般材料厚度追溯遵循其文件的长度单位。
        if self.kind == "thickness" and self.unit not in ("mm", "inch"):
            raise ValueError("dielectric thickness requires unit mm or inch")
        if self.kind is not None and self.input_value is None:
            raise ValueError("thickness provenance requires input_value")
        return self


# 唯一层记录：层身份 + 图案 + 计算域引用 + 绝对 Z / 来源 / 拓扑跨度。
# 完整目录也保留非物理层、失败层及未导出层，供消费方准确诊断。
class LayerModel(ContractModel):
    # 层名须非空；跨层名称唯一性在 StepModel 中校验。
    name: str = Field(min_length=1)
    # 保留 ODB++ 类型字符串；不强制转换为铜 / FR4 两类，未知材料须明确跳过。
    type: str = Field(min_length=1, description="ODB++ 层类型；等效器明确报告不支持的材料类型。")
    # 只有 BOARD 上下文中的适用材料层参与当前等效。Parser 应保留原上下文。
    context: Literal["BOARD", "MISC"] = "BOARD"
    # matrix row 是层目录排序依据；模型检查正整数，不在这里校验排序或 row 唯一性。
    row: StrictInt = Field(default=1, ge=1)
    # 刚性 / 柔性身份；form_source 区分显式声明、FLEX 子类型与规范默认。
    form: Literal["RIGID", "FLEX"] = "RIGID"
    form_source: Literal["DECLARED", "FLEX_SUBTYPE", "DEFAULT"] = "DEFAULT"
    # ready 可以是有效空图案；failed 是解析失败；omitted 是没有交付该层几何。
    geometry_status: Literal["ready", "failed", "omitted"] = "ready"
    # 失败原因直接随层携带；根诊断列表不进入文件，仍不能丢失失败语义。
    geometry_error: str | None = None
    # 引用实际层域；结构允许 None 表达不可用状态，正式物理层不能缺有效域。
    domain_id: str | None = None
    # 图案列表必填。介质保留未扣跨层 DRILL 的几何；铜层只含本层规整铜图案。
    polygons: list[PolygonModel] = Field(description="本层图元规整后的正向几何；介质未扣除跨层 DRILL 投影，铜层不受 DRILL 改写。")
    # 非实体层可为空；正式实体物理层必须交付绝对 Z；DRILL 由端点铜层推导。
    z_mm: ZExtentModel | None = Field(default=None, description="实体物理层自身的绝对 Z；DRILL 不保存此字段，由 copper_span 端点铜层推导。")
    # 本层厚度来源，供追溯；几何数值厚度只从 z_mm 的起止差取得。
    thickness: ThicknessModel | None = None
    # BOARD 铜层自顶向下索引，从零开始；不要给 DRILL 分配独立铜层索引。
    copper_index: Index | None = None
    # 介质或 DRILL 的两个铜层端点；长度、递增关系与引用有效性分阶段检查。
    copper_span: list[Index] | None = Field(default=None, min_length=2, max_length=2,
                                          description="自顶向下的两个铜层索引；不重复存储端点名称。")

    @model_validator(mode="after")
    # 单层语义检查：递增跨度、失败 / 未导出不带几何、失败必须有原因。
    # 没有在这里保证必要 domain / Z 存在，消费方仍有正式等效的输入要求。
    def consistent(self):
        # 禁止用可推导的重复 Z 描述钻孔，避免与端点铜层的位置发生矛盾。
        if self.type == "DRILL" and self.z_mm is not None:
            raise ValueError("DRILL must not carry z_mm; derive it from copper_span endpoint copper layers")
        if self.copper_span is not None and self.copper_span[0] >= self.copper_span[1]:
            raise ValueError("copper_span endpoints must increase")
        if self.geometry_status != "ready" and self.polygons:
            raise ValueError("failed/omitted geometry must not carry polygons")
        if self.geometry_status == "failed" and not self.geometry_error:
            raise ValueError("failed geometry requires geometry_error")
        return self


# 一个实体 step，统一登记计算域和完整层目录；不展开拼板引用。
class StepModel(ContractModel):
    # 单板实体 step 名称。
    name: str = Field(min_length=1)
    # 整板 profile 引用，可为空表达轮廓缺失；实际整板几何规划需要有效 profile。
    profile_domain_id: str | None = Field(default=None, description="整板 step profile 在 domains 中的引用；不能以铜图案替代。")
    # 域目录存储轮廓；多个层可共用同一条目。
    domains: list[DomainModel]
    # 不能仅列出成功非空的铜层；省略失败物理层会隐藏输入缺陷。
    layers: list[LayerModel] = Field(description="唯一层目录；包含失败及未导出层，按 matrix row 排列。")

    @model_validator(mode="after")
    # 在本 step 范围内检查唯一标识与引用关系，不执行几何运算。
    def references(self):
        # 域 id 和层名分别唯一；具体 id 文字不硬编码为 step_profile。
        ids = [d.id for d in self.domains]
        names = [l.name for l in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate domain id")
        if len(names) != len(set(names)):
            raise ValueError("duplicate layer name")
        domains = {d.id: d for d in self.domains}
        # 只有声明索引的层进入铜层索引集合；索引不允许重复。
        indexed = [l for l in self.layers if l.copper_index is not None]
        copper = {l.copper_index for l in indexed}
        if len(copper) != len(indexed):
            raise ValueError("duplicate copper_index")
        # 索引所属对象必须是 BOARD 铜层；这不检查所有铜层是否都声明了索引。
        if any(l.type not in ("SIGNAL", "POWER_GROUND", "MIXED") or l.context != "BOARD" for l in indexed):
            raise ValueError("copper_index must belong to a BOARD copper layer")
        # 声明整板引用后，它必须存在，且其 domain 来源必须是 step_profile。
        if self.profile_domain_id is not None:
            if self.profile_domain_id not in domains or domains[self.profile_domain_id].source != "step_profile":
                raise ValueError("profile_domain_id must reference a step_profile domain")
        # 逐层引用有效性：域必须存在，跨度端点必须对应实际登记的铜层索引。
        # 这些检查不保证跨度与真实 matrix START_NAME / END_NAME 相符，Parser 必须正确解算。
        for layer in self.layers:
            if layer.domain_id is not None and layer.domain_id not in domains:
                raise ValueError(f"layer {layer.name!r}: unknown domain_id {layer.domain_id!r}")
            if layer.copper_span is not None and any(i not in copper for i in layer.copper_span):
                raise ValueError(f"layer {layer.name!r}: copper_span endpoints must reference existing copper_index values")
        return self


# 输入产品来源：ODB++ 版本属于输入格式追溯，不是中间 JSON 的业务版本。
class ProductModel(ContractModel):
    name: str | None = None
    odb_version: str


# 输入位置与单位来源。下游不能依据 input_units 再换算已归一化的 mm 几何。
class SourceModel(ContractModel):
    path: str
    job_root: str
    input_units: Literal["MM", "INCH"]
    product: ProductModel


# 顶层接口容器：统一 mm、恰好一个单板 step，以及可选来源。
class BoardDocument(ContractModel):
    """One physical step, positive polygons, mm XY and absolute downward Z."""
    # 固定输出单位。该字段的验证不能发现数字曾被错误换算，Parser 仍承担单位正确性。
    units: Literal["mm"]
    # 允许手工合成数据省略；实际 Parser 导出记录输入来源。
    source: SourceModel | None = None
    # 最终几何的坐标量化间距，与弧弦高容差 / 材料光栅分辨率分开。
    # 模型不按它自动舍入坐标或重新布尔规整，Parser 应先执行这些操作。
    resolution_mm: Number = Field(default=0.0001, gt=0,
                                  description="规整后坐标网格间距，单位 mm；非材料采样光栅。")
    # 当前接口只支持一个单板 step。
    steps: list[StepModel] = Field(min_length=1, max_length=1)

    # 等效器把已验证对象映射成算法使用的普通字典；这不是完整文件导出 API。
    # 有意省略 source / thickness / form / resolution_mm 等非当前循环输入。
    # 只重建少量映射容器，points 直接复用；算法必须避免修改共享坐标列表。
    def internal_document(self) -> dict:
        """Drop the Pydantic containers without copying large coordinate lists.

        Consumers immediately construct their existing Ring/Region dataclasses.
        Returned mappings belong to this one computation; no validation in loops.
        """
        # step 域与层图案仍保留显式 island 语义；每个点不会额外构造模型对象。
        return {"units": self.units, "steps": [{
            "name": step.name, "profile_domain_id": step.profile_domain_id,
            "domains": [{"id": d.id, "source": d.source,
                         "polygons": [{"island": p.island, "points": p.points} for p in d.polygons]}
                        for d in step.domains],
            "layers": [{"name": l.name, "type": l.type, "context": l.context,
                        "row": l.row, "geometry_status": l.geometry_status,
                        "geometry_error": l.geometry_error, "domain_id": l.domain_id,
                        "copper_index": l.copper_index, "copper_span": l.copper_span,
                        # 小型 Z 记录转为普通 dict；体积厚度依赖这里的绝对起止值。
                        "z_mm": None if l.z_mm is None else l.z_mm.model_dump(),
                        "polygons": [{"island": p.island, "points": p.points} for p in l.polygons]}
                       for l in step.layers]} for step in self.steps]}


# 普通 dict 入口会验证完整契约；已有 BoardDocument 视为已验证实例直接返回。
# 因此不能用 model_construct() 或修改其嵌套列表后把它当安全外部输入。
def validate_document(document: BoardDocument | Mapping[str, Any]) -> BoardDocument:
    return document if isinstance(document, BoardDocument) else BoardDocument.model_validate(document)


# 文件入口直接从 JSON 字节构造并验证模型；不读取 .schema，不兼容旧字段。
# IO 异常与 ValidationError 由调用方处理并给出可定位诊断。
def read_document(path: str | Path) -> BoardDocument:
    return BoardDocument.model_validate_json(Path(path).read_bytes())


# 写出已验证模型，省略 None 字段；bool / 默认值仍按模型内容保存。
# 不在这里重做验证、换算单位或创建目录。完整来源信息保留在文件模型中。
def write_document(document: BoardDocument, path: str | Path) -> None:
    Path(path).write_text(document.model_dump_json(indent=1, exclude_none=True) + "\n", encoding="utf-8", newline="\n")


# 从同一模型生成接口文本，供阅读 / 外部工具 / 同步测试使用。
# JSON Schema 不能完整表达上面的 Python 跨字段校验器。
def write_schema(path: str | Path) -> None:
    """Generate the unversioned, human-readable JSON Schema from this contract."""
    import json
    schema = BoardDocument.model_json_schema()
    # 这个 URI 声明 JSON Schema 标准语法；没有业务版本比较，也不会访问该 URI。
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    Path(path).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
```

### 附录核对记录

本次核对了附录与源码的完整 AST、生成模型 schema 一致性，并执行附录模型验证完整 JSON 示例。
示例经等效器生成 45 个三维物性单元；介质 DRILL 覆盖与空下铜图案语义均通过检查。
其余 Python 示例已检查可编译，本文所有本地文件链接均可解析。
