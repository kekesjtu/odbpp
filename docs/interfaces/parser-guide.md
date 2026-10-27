# 中间 JSON：ODB++ 几何解析器开发指南

本文面向负责 ODB++ 文本读取、符号展开、图元极性及几何规整的开发人员。目标是明确解析器需要交付什么数据、为何需要这些字段，以及如何通过当前 `intermediate.py` 输出可被等效器正确理解的实体模型。

源码依据：[`contracts/intermediate.py`](../../contracts/intermediate.py)。模型源码集中维护，字段参考见 [中间 JSON](intermediate-json.md)。

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

原始层极性、图元数量和极性段计数保留于解析器内部统计中。根节点不导出 `diagnostics`，诊断保留在内部模型、CLI 或独立报告中。`geometry_status`、厚度可信状态及来源保留，因为它们决定下游能否把某个几何结果视为可用实体。

## 2. Pydantic 模型与 .schema 各负责什么

| 对象 | 当前职责 | 解析器开发人员如何使用 |
|---|---|---|
| `intermediate.py` 中的模型 | 定义字段并执行类型、范围与跨字段关系校验 | 在导出边界构造 `BoardDocument`；不要为每个原始图元构造模型 |
| `docs/interfaces/intermediate.schema` | 从模型生成的 JSON Schema 文本，供接口阅读及外部工具使用 | 查字段形状；随模型变更重新生成，不手工维护另一套定义 |
| `tests/contracts/test_intermediate.py` | 检查导入约束、round-trip 和 schema 与模型的一致性 | 修改接口后执行相应测试 |
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

已有实现中的导出入口是 `export.py::step_to_document()`，profile 域构建为 `export.py::_domains_json()`，厚度及跨度解析为 `stackup.py`。这些函数负责把内部解析结果映射到本文接口，在 Pydantic 结构校验之前完成几何算法。

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
from pcbkit import load_job, build_step_preview
from pcbkit.export import step_to_document
from pcbkit.intermediate import read_document, write_document

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
from pcbkit.intermediate import BoardDocument, write_document

# payload 是你的解析器构造的完整当前格式 dict。
# 大文件可按层构造 LayerModel，避免未验证坐标树与模型坐标树同时长期驻留。
document = BoardDocument.model_validate(payload)
Path("build").mkdir(exist_ok=True)
write_document(document, "build/pcb.json")
```

`write_document()` 接受已验证模型，不主动创建目录，也不重新解释图元或转换单位。调用方负责创建输出目录与处理写入异常。收到 `ValidationError` 应保留其字段路径和错误原因；不要捕获后填默认矩形或空图案继续输出一个伪成功结果。

`internal_document()` 是等效器进入内部算法的轻量映射。它复用坐标列表，只保留运算所需字段；来源、厚度追溯、form 等不会全部进入这个映射。文件导出应使用 `write_document()`，不应把 `internal_document()` 当作完整文件序列化 API。

## 10. 修改接口与交付检查

编辑 `contracts/intermediate.py` 后，同步两侧包内模型与 schema；在导出边界检查正向几何、完整目录、逐层计算域、Z 来源和铜层跨度。

```powershell
uv run --all-extras python tools/sync_contract.py
uv run --all-extras pytest tests/contracts tests/distribution
uv run --all-extras python tools/sync_contract.py --check
```

涉及真实几何或物理语义的变更时，按 [开发说明](../development.md) 重导出案例并审计完整单元。
源码的跨字段校验器、本文合成例子与 [接口字段参考](intermediate-json.md) 共同说明交付语义。
