# 03 · 测试用例普查与实测观察

> 输入：`cases/` 中的 4 个 ODB++ v8.1 样本。
> 目的：以**实测数据**校验 `01`/`02` 两份规范推导出的实现假设，并暴露规范未覆盖的工程风险。

---

## 1. 普查总表

| 指标 | minimal-test-odb | kicad-kitchen-sink | kicad-video-odb | designodb-rigidflex |
|---|---|---|---|---|
| 形态 | 展开目录 | 展开目录 | `.tgz` | `.tgz` |
| 体积 | 9 KB | 358 KB | 1.2 MB | 12 MB（解包 57 MB） |
| 文件数 | 9 | 83 | 66 | 839 |
| `UNITS` | `MM` | `MM` | `MM` | **`INCH`** |
| `ODB_VERSION` | 8.1 | 8.1 | 8.1 | 8.1 |
| `ODB_SOURCE` | ODB++ Test Generator | KiCad EDA | KiCad EDA 9.0.3 | Xpedition xPCB Layout |
| `SAVE_APP` | — | KiCad EDA 9.99.0 | KiCad EDA 9.0.3 | Valor NPI 10.0 |
| Step 数 | 1 (`pcb`) | 1 (`pcb`) | 1 (`pcb`) | 1 (`cellular_flip-phone`) |
| matrix 层数 | 2 | 37 | 28 | 43 |
| 铜层 | 2 | 2 | 4 | 10 |
| 介质层 | 0 | 1 | 3 | 9 |
| 钻孔层 | 0 | 1 | 1 | 7 |
| 自定义符号 | 0 | 0 | 0 | **123** |

### 1.1 层类型分布（来自各自 `matrix/matrix`）

```
minimal-test-odb     SIGNAL 2
kicad-kitchen-sink   DOCUMENT 26 | SIGNAL 2 | SILK_SCREEN 2 | SOLDER_MASK 2 | SOLDER_PASTE 2
                     | COMPONENT 1 | DIELECTRIC 1 | DRILL 1
kicad-video-odb      DOCUMENT 12 | SIGNAL 4 | DIELECTRIC 3 | COMPONENT 2 | SILK_SCREEN 2
                     | SOLDER_MASK 2 | SOLDER_PASTE 2 | DRILL 1
designodb-rigidflex  SIGNAL 7 | DIELECTRIC 9 | DRILL 7 | DOCUMENT 4 | SOLDER_MASK 2(+COVERLAY 2)
                     | SOLDER_PASTE 2 | POWER_GROUND 1(+PG_FLEX 2) | COMPONENT 2 | ROUT 1
                     | MASK 4 (BEND_AREA / FLEX_AREA / RIGID_AREA / STIFFENER)
```

覆盖了 spec 表 5-2（p.65）中除 `CONDUCTIVE_PASTE`、`SOLDER_MASK` 之外的所有基类型；
`designodb-rigidflex` 额外覆盖了 6 种 `ADD_TYPE` 子类型。

---

## 2. 逐例观察

### 2.1 `minimal-test-odb`

- 仅 2 个铜层，`F 0`/无钻孔/无符号。
- 价值：**最小闭环回归**。任何格式化/IO 层改动都应先跑通此例。

### 2.2 `kicad-kitchen-sink`

**(a) 介质层不含几何 —— 印证 AGENTS 约束 3 的前提**

```
steps/pcb/layers/dielectric_1/features
    F 0
```

只有一个 `DIELECTRIC 1` 层（`DIELECTRIC_1`），其 `features` 特征数为 **0**。
这说明 KiCad 导出的介质层**完全不携带几何**，介质基底只能从板框推导，
与 AGENTS 约束 3「介质层的基底多边形直接继承板框文件 `steps/<step>/profile`」完全一致。

**(b) 无任何层级 `profile`**

遍历 `steps/pcb/layers/*/`，**没有任何**层存在 `profile` 文件。
板框的唯一来源是 `steps/pcb/profile`（`F 1`，单 island）。
→ 对 KiCad 系产物，约束 3 是充分且必要的。

**(c) matrix 与文件系统不一致（**关键**）**

`steps/pcb/layers/` 下有 **37 个**目录，matrix 中也有 **37 个** `LAYER` 块，
但**名字大小写不同**：

| | matrix `NAME` | 磁盘目录名 |
|---|---|---|
| step | `PCB` | `pcb` |
| 铜层 | `F.CU` / `B.CU` | `f.cu` / `b.cu` |
| 介质 | `DIELECTRIC_1` | `dielectric_1` |

在 Windows 上这「碰巧能跑」，在 Linux 上必然失败。**必须实现大小写不敏感的实体名解析。**

**(d) 存在「孤层」：磁盘上有、matrix 中没有**

即使忽略大小写，下面 4 个层目录也**完全不在 matrix 中**：

```
capping_f.cu-b.cu     (F=1)
filling_f.cu-b.cu     (F=1)
plugging_front        (F=1)
tenting_front         (F=2)
tenting_back          (F=1)
```

这些是 **IPC-4761 过孔保护**相关层（盖帽/填充/塞孔/盖油），KiCad 会导出但未登记进 matrix。
→ **matrix 是层清单的权威来源**；不在 matrix 中的层应**忽略并记录 warning**，
不可静默纳入（否则层数与叠层结构会和下游 CAM 不一致）。

**(e) 属性表规模可观**

`dielectric_3` 类层（本例为 `f.mask` 等）的 `features` 里 `@` 属性名表可达 30+ 项
（`.pad_usage`、`.geometry`、`.smd`、`.nomenclature`、`.drc_*` 等）。
→ 属性两级间接寻址（`01` 第 4.3 节）必须实现，且需容忍**未在 `sysattr*` 中声明的属性名**。

### 2.3 `kicad-video-odb`

- 首个真正多层的用例：4 铜层（`top_copper` / `in1.cu` / `in2.cu` / `b.cu`）+ 3 介质层。
- 钻孔层 `DRILL_PLATED_TOP_COPPER-B.CU`，跨度 `START_NAME=TOP_COPPER` → `END_NAME=B.CU`。
- → 用于验证「介质层 ↔ 铜层 Z 区间映射」与「钻孔跨越哪些介质层」的中间层情形。

### 2.4 `designodb-rigidflex`（最严苛，暴露规范外风险）

**(a) INCH 双单位并存的实证**

```
steps/cellular_flip-phone/layers/signal_1/features
    UNITS=INCH
    $0 r10.827                      # 10.827 mil  → 0.275 mm
    $1 donut_r78.74x27.559          # OD 2.000 mm / ID 0.700 mm

steps/cellular_flip-phone/layers/d_1_10/tools
    UNITS=INCH
    FINISH_SIZE=78.7402             # 78.7402 mil → 2.000 mm
    DRILL_SIZE=78.7402
```

坐标用 inch（`OB -0.494322244094 ...`），符号/刀具尺寸用 mil。
→ **完全印证 `01` 第 3.3 节的「坐标与符号参数单位不同」规则**。

**(b) 钻孔跨度的全部情形**

| 钻孔层 | START_NAME | END_NAME | 类型 |
|---|---|---|---|
| `D_1_10` | `SIGNAL_1` | `SIGNAL_10` | 通孔 |
| `D_1_2` | `SIGNAL_1` | `SIGNAL_2` | 盲孔（顶层起） |
| `D_2_3` | `SIGNAL_2` | `SIGNAL_3` | 埋孔 |
| `D_3_8` | `SIGNAL_3` | `SIGNAL_8` | 埋孔（跨 5 层） |
| `D_8_9` / `D_9_10` | `SIGNAL_8` / `SIGNAL_9` | `SIGNAL_9` / `SIGNAL_10` | 盲孔（底层起） |
| `D_5_6` | `FLEX_5` | `FLEX_6` | **挠性区埋孔** |

其中 `FLEX_5` / `FLEX_6` 是 `FORM=FLEX` 的铜层。
→ 实现「钻孔 → 介质层投影」时必须先**按 matrix 行序把钻孔层展开成连续的铜层区间**，
再与介质层的 Z 区间求交；不能只按名字数字排序。

**(c) 同一个钻孔层混合 PLATED 与 NON_PLATED 刀具**

```
steps/.../layers/d_1_10/tools
    TYPE=NON_PLATED  × 3
    TYPE=PLATED      × 1
```

`d_1_10` 的 `features` 只录入了 5 条 `P` 记录，而 `tools` 定义了 4 把刀。
→ 钻孔图元的「是否金属化」应取自 **tool 的 `TYPE`**，而非层名。层名（`DRILL_PLATED_*`）
只是约定，不可依赖。

**(d) 介质层携带自己的 `profile`（**与 AGENTS 约束 3 冲突**）**

在 43 个层目录中，**23 个**存在层级 `profile`，涵盖全部 9 个介质层：

```
dielectric_3, dielectric_5, dielectric_7, dielectric_9, polyimide_base_11,
dielectric_13, dielectric_15, dielectric_17, dielectric_19,
signal_1, signal_2, signal_3, signal_7, signal_8, signal_9, signal_10,
plane_4, flex_5, flex_6, covertop, coverbottom, soldermask_top, soldermask_bottom
```

且**层级 `profile` 的包围盒与 `steps/<step>/profile` 不同**（单位 inch）：

| 来源 | bbox (minX, minY, maxX, maxY) |
|---|---|
| `steps/cellular_flip-phone/profile` | `(-3.3543, -0.0866, 3.0768, 1.6378)` |
| `layers/signal_1/profile` | `(-0.1673, -0.0866, 3.0768, 1.6378)` |
| `layers/dielectric_3/profile` | `(-0.1673, -0.0866, 3.0768, 1.6378)` |
| `layers/flex_5/profile` | `(-3.3543, -0.2498, 3.0768, 1.6378)` ← 与 step 一致 |
| `layers/coverbottom/profile` | `(-3.3543, -0.2498, 3.0768, 1.6378)` ← 与 step 一致 |

**这是刚挠结合板的本质**：整板轮廓（step profile）是「刚性区 ∪ 挠性区」，
而**单个介质层只存在于其中一部分**。若机械套用约束 3（介质基底一律继承 step profile），
`dielectric_3` 会被错误地扩展到挠性区域，产生**假铜/假介质**。

> **建议（待确认）**：把约束 3 细化为
> **「若 `<layer_name>/profile` 存在，则以其为该层基底；否则回退到 `steps/<step>/profile`」**。
> 这与规范一致（p.181 定义了层级 profile），且对 KiCad 用例（无层级 profile）行为不变。

**(e) 123 个自定义符号 + 嵌套/缩放**

`symbols/` 下 123 个符号目录，每个含 `features` + `.features.sum`。
示例 `sc_join0201_hd/features` 用 `S` surface + `OB/OS/OC` 混排描述圆角轮廓：

```
S P 0 ;;ID=146
OB -0.005511811024 -0.00374015748 I
OS -0.005511811024 0.00374015748
OC -0.002755905512 0.006496062992 -0.002755905512 0.00374015748 Y
OS 0.002755905512 0.006496062992
OC 0.005511811024 0.00374015748 0.002755905512 0.00374015748 Y
...
```

→ 直接验证 AGENTS 约束 5「自定义 `symbols/` 需递归读取其几何定义」，
且证明**自定义符号内部同样会用到 `OC` 圆弧**，离散化逻辑必须复用。

**(f) 存在 `.sum` 校验副产物**

每个 `features`/`profile`/`attrlist` 旁都有 `.xxx.sum`：

```
SIZE = 691
SUM = 58168
DATE = 10/24/2016
TIME = 10:14:54
VERSION = EEVX
USER = saram
```

规范正文**未定义** `SUM` 的算法。→ 本阶段忽略，仅可选地做 `SIZE` 校验。

---

## 3. 从实测提炼的实现要求（Checklist）

| # | 要求 | 依据 | 优先级 |
|---|---|---|---|
| R1 | 实体名（step/layer/symbol）**大小写不敏感**解析 | 2.2(c) | 必须 |
| R2 | **matrix 是层清单权威**；孤层忽略 + warning | 2.2(d) | 必须 |
| R3 | 符号参数与 `tools` 尺寸用 **mil/µm**，坐标用 **inch/mm** | 2.4(a) | 必须 |
| R4 | 层基底优先取 `<layer>/profile`，缺失才回退 `steps/<step>/profile` | 2.4(d) | 必须（需确认） |
| R5 | 钻孔金属化取 **tool.TYPE**，不取层名 | 2.4(c) | 必须 |
| R6 | 钻孔 Z 跨度按 **matrix 行序**展开为铜层区间后与介质层求交 | 2.4(b) | 必须 |
| R7 | 属性名/字符串表**两级间接寻址**，容忍未声明属性 | 2.2(e) | 必须 |
| R8 | 自定义符号**递归展开 + 环检测 + 缩放因子** | 2.4(e) | 必须 |
| R9 | `features`/`profile` 可能被 **gzip** 压缩，按魔数判定 | `01` §2.3 | 必须 |
| R10 | 记录内 `;` 属性段与 `;ID=` **可选**，不可按固定列切分 | `01` §4.4 | 必须 |
| R11 | `UNITS` 缺失时按**英制**兜底 | `01` §3.1 | 必须 |
| R12 | copper weight 语义随单位制变化，**不可用常数换算** | `01` §3.2 | 必须 |
| R13 | 自交多边形检测 + 修复 + warning | `02` §3.3 | 建议 |
| R14 | `.sum` 文件忽略 | 2.4(f) | 建议 |
| R15 | `stephdr` 嵌套 step 阵列：按 AGENTS 约束 1 **不支持**，必须显式报 warning | `01` §6 | 必须 |
| R16 | 标准/自定义符号按**严格文法**消歧，**禁止前缀匹配** | 4.2(a) | 必须 |
| R17 | 符号**惰性加载 + 缓存**，未引用符号不影响解析 | 4.2(b) | 建议 |

---

## 4. 图元普查（`tools/census_cases.py`）

对 4 个用例的全部 `features` / `profile` 文件（含 `symbols/` 内的定义）做逐记录统计：

| 用例 | P | L | A | S | T | B | 负极性记录 | OB island | OB hole |
|---|---|---|---|---|---|---|---|---|---|
| `minimal-test-odb` | 21 | 6 | 2 | 1 | 0 | 0 | `A:N=1` | 1 | 0 |
| `kicad-kitchen-sink` | 55 | 358 | 2 | 4 | 0 | 0 | 0 | 4 | 3 |
| `kicad-video-odb` | 13946 | 29720 | 21 | 21 | 0 | 0 | 0 | 21 | 0 |
| `designodb-rigidflex` | 41550 | 471158 | **958** | 5274 | 0 | 0 | **`A:N=308`** | 5274 | **987** |

符号名前缀分布：

```
minimal-test-odb    r=6, s=3, rect=3, oval=2
kicad-kitchen-sink  r=37, rect=14, donut_rc=3, donut_r=3
kicad-video-odb     r=118, rect=94, oval=12, donut_r=5, donut_rc=5
designodb-rigidflex rect=328, r=158, donut_r=4, + 9 个自定义符号
```

### 4.1 由此确认的结论

1. **负极性记录真实存在**且数量可观：`designodb-rigidflex` 有 **308 条 `A:N`**（负弧），
   `minimal-test-odb` 有 1 条。→ `02` §2.1 的**保序差集**逻辑**必须实现**，
   且冒烟用例（`minimal-test-odb`）就带一条，适合做最小回归。
2. **`OC` 圆弧用量巨大**：`designodb-rigidflex` 共 958 条弧记录，其中 308 条为负。
   → 圆弧离散化不是边缘功能，而是主干路径。
3. **孔洞（`OB ... H`）真实存在**：987 个 hole polygon。→ 方向约定（island CW / hole CCW）
   与「外环 − 内环」的构造必须有回归。
4. **matrix 层极性全为 `POSITIVE`**：4 个用例均无负片层。
   → `02` §2.2 的**层级负片分支无测试数据**（见 §5 缺口 1）。

### 4.2 自定义符号解析的关键实测

对 `designodb-rigidflex` 的 `$` 声明做符号归属分析：

| | 数量 |
|---|---|
| `$` 声明总数 | 499 |
| 判定为标准符号 | 490 |
| 判定为自定义符号 | 9 |
| `symbols/` 磁盘上的符号定义目录 | **123** |
| 被层文件实际引用的自定义符号 | **9** |
| 被引用但 `symbols/` 中缺失 | 0 |
| 在磁盘但未被引用 | **114** |

被层文件引用的 9 个自定义符号：

```
sc_join0201_hd
sc_join0201_hd_sm_50um
special_0.670x0.950_nt2520
special_0.770x0.840
special_0.770x1.050_nt2520
special_byx101517
special_byx101517_sm
special_byx101517_sp_b
special_spark_gap_0.457x0.229
```

**两条重要结论：**

**(a) 「以 `s` 开头」不等于「方符号」——标准/自定义符号必须靠严格文法消歧。**
上表中的 `special_0.670x0.950_nt2520` 以 `s` 开头，但**不是** `s<side>` 方符号。
规范对此的判据是：标准符号名的参数部分必须能按 `<prefix>[<num>[x<num>...]]` 解析成功
（p.36）；解析失败即视为自定义符号名，去 `symbols/` 目录查找。
**绝不能**用「首字母/前缀匹配」来判定标准符号。

**(b) 符号必须惰性加载。** 123 个定义中只有 9 个被引用（7.3%）。
若在解析阶段全量递归展开全部符号，会做 13 倍的无效工作（本例 57 MB 数据）。
→ **按 `$` 引用集合惰性加载 + 缓存**。同时，未被引用的符号不得导致解析失败。

**(c) 本例未出现自定义符号的嵌套引用。** 在 `symbols/*/features` 内部，
`$` 声明全部是标准符号（自定义 0 个）。
→ AGENTS 约束 5 的「递归」在现有数据中**未被触发**，见 §5 缺口 3。

---

## 5. 覆盖缺口（需要补充用例 / 需自造夹具）

| # | 缺口 | 现状 | 影响 |
|---|---|---|---|
| 1 | 层级 `POLARITY=NEGATIVE`（负片电源/地层） | 4 例全为 `POSITIVE` | `02` §2.2 层间负片分支**零覆盖** |
| 2 | `T`（Text）/ `B`（Barcode）图元 | **4 例中出现次数均为 0** | 字体矢量展开、条码生成**零覆盖** |
| 3 | 自定义符号**嵌套引用** | 现有 9 个被引用符号内部只用标准符号 | 递归 + 环检测**零覆盖** |
| 4 | 符号 **resize factor**（`apt_def = -1 <sym> <resize>`） | 未出现 | 缩放路径**零覆盖** |
| 5 | `CONDUCTIVE_PASTE` 层、`CARBON_MASK`/`SILVER_MASK` 子类型 | 未出现 | 低优先 |
| 6 | 非对称符号绘制 Line 的异常情形 | 未出现 | 需构造夹具 |
| 7 | 多 step / 嵌套 step 阵列（`stephdr` 的 `DX/DY/NX/NY`） | 4 例均为单 step | AGENTS 约束 1 明确排除，但**warning 触发路径零覆盖** |
| 8 | 大符号族：`hex_*`/`tri`/`thr`/`ths`/`s_ths`/`rc_ths`/`hplate`/`cross`/`dogbone`/`dpack`/`moire` | 4 例只用到 `r`/`s`/`rect`/`oval`/`donut_r`/`donut_rc` | Appendix A 大部分符号无回归 |
| 9 | `wheels/`（Gerber 轮）、`stackup.xml`、`impedance.xml` | 本阶段不实现 | 明确排除 |

> **缺口 2、3、4、6、7、8 需自造夹具**：用最小手写 ODB++ 目录（可直接复用
> `cases/minimal-test-odb` 的骨架）逐特性构造，作为单元测试输入，而非依赖外部样本。
> 建议目录：`tests/fixtures/`，与本清单分离，避免污染 `cases/`（真实样本目录）。

---

## 6. 结论

1. **`01`/`02` 两份规范推导与实测数据一致**的关键点：单位双轨制（坐标 mm/inch、
   符号参数 µm/mil）、介质层无几何、`OC` 圆弧出现在层与符号内部、属性两级寻址、
   负极性记录的保序差集语义。
2. **需要修订的一条假设**：AGENTS 约束 3 的「介质基底一律继承 step profile」在
   KiCad 系数据上成立，但在生产级刚挠结合板上会出错。建议改为「层级 profile 优先」。
3. **需要新增的三条硬要求**：
   - R1 实体名大小写不敏感（Linux 上不改必失败）；
   - R2 matrix 为层清单权威；
   - R16 **标准/自定义符号靠严格文法消歧，禁止前缀匹配**（`special_*` 反例）。
4. **符号必须惰性加载**：123 个定义中仅 9 个被引用。
5. `designodb-rigidflex` 虽体积较大（12 MB 压缩 / 57 MB 解包），但它是唯一同时覆盖
   **英制单位 + 盲埋孔 + 刚挠结合 + 负极性圆弧 + 987 个孔洞**的用例，**不可替代**，
   应保留为端到端验收用例。
