# odbpp

ODB++ v8.1 → 统一的公制多边形模型（JSON）/ 分层矢量预览（SVG）→ PCB 物性等效（MaterialMap），
并为等效结果输出可直接观看的逐层黑白掩膜 PNG 与 `kx`/`ky`/`kz` 热图 PNG。

当前状态：**五阶段数据流全部可用**。读取目录、`.tgz`/`.tar.gz`、`.zip` 形式的
ODB++ v8.1 job，展开图元与符号，用布尔运算合成**完全正向**的公制多边形，
输出中间 JSON 与带图层开关、缩放平移的 HTML/SVG 预览；再从 JSON 出发，
把铜层/介质层等效为逐网格的正交各向异性物性表，并附上逐层掩膜与热图。

```
[ ODB++ 压缩包/目录 ]
        │
        ▼
[ 1. ODB++ Parser ]  ← 已实现  load_job()
        ├──────────────────────────────────────────┐
        ▼                                          ▼
[ 2. 中间格式 (.json) ]                [ 3. 分层矢量预览 (.svg/.html) ]
  已实现  build_step_preview()            已实现  render_svg() / render_html()
          step_to_json()                          write_outputs()
   schema = odbpp/preview-2（含逐层计算域）
        │
        ▼
[ 4. PCB 物性等效器 ]  ← 已实现  equivalence_from_document()
        │
        ▼
[ 5. 等效结果 ]  MaterialMap.csv + MaterialMap.meta.json   ← 数值唯一依据
        │
        └─ 只给人看：masks/<layer>.png、heatmaps/<layer>.png、<step>.images.html
```

四个真实样本均已跑通，含 520k 图元 / 43 层的 INCH 刚挠结合板
（导出约 150 s，含 DOCUMENT 层）。介质层按 AGENTS 约束 3 扣除与其 Z 区间相交的钻孔投影，
输出坐标在目标分辨率下重新量化与布尔规整后才写出。

## 快速开始

```bash
uv sync --all-extras         # 按 uv.lock 安装项目及测试/工具依赖到 .venv/

# 结构总览（层清单、图元类型统计、诊断）
uv run --all-extras python -m odbpp summarize cases/kicad-kitchen-sink

# 机器可读的结构转储
uv run --all-extras python -m odbpp summarize cases/kicad-kitchen-sink --format json --json build/ks-summary.json

# 其它子命令
uv run --all-extras python -m odbpp info        <path>          # job root / 单位 / ODB++ 版本
uv run --all-extras python -m odbpp layers      <path> [--step S]
uv run --all-extras python -m odbpp symbols     <path>
uv run --all-extras python -m odbpp diagnostics <path>

# 同步 cases/ 对应的预览、等效 CSV、逐层掩膜/热图及 out/index.html
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid --prune
# 需要其他分辨率/网格时显式指定
uv run --all-extras python tools/sync_out.py --grid-level fine
# 只重算物性等效与图片，或强制重建全部结果
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid --force-equivalent
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid --force

# 临时或筛选导出放在 build/，不覆盖 out/ 的完整案例产物
uv run --all-extras python -m odbpp export cases/designodb-rigidflex.tgz -o build/rigidflex-filtered --exclude-type DOCUMENT --per-layer-svg --minimum-stroke 0.05
uv run --all-extras python -m odbpp export <path> -o build/selected-layers --layer top --layer bottom --only-json
```

网格必须为全局笛卡尔栅，所有竖条阶梯 region 边界进入共同的 X/Y 轴线。
常规采用粗/标准/细档位，依据板型与铜图案特征尺度自适应，默认标准；
专家通过最小/最大边长控制，单位 mm，各接受一个值或 X Y 两个值。
默认密集光栅在几何确定后按采样容量加密，显式低分辨率会报错。
region 和网格坐标不随光栅变化。算法与预算见
[当前架构](docs/equivalence/05-inscribed-architecture.md)。

`export` 的主要选项：

| 选项 | 作用 |
|---|---|
| `--layer NAME` / `--exclude-type TYPE` | 选择要合成的层（可重复） |
| `--sagitta MM` | 圆弧离散化容差，默认 0.005 mm |
| `--digits N` | 坐标小数位，默认 4（0.1 µm）；同时是模型的**输出分辨率**：几何会先量化到该网格、布尔规整并复验拓扑，再序列化 |
| `--minimum-stroke MM` | 把零宽（`r0`）图元按发丝线绘制；默认 0，即几何精确因而不可见 |
| `--only-json` / `--per-layer-svg` | 只要 JSON / 额外每层一个 SVG |

物性等效（数据流 4-5）：

```bash
# 一步到位：解析 + 等效。输入可以是 ODB++ 目录/压缩包
uv run --all-extras python -m odbpp equivalent cases/kicad-kitchen-sink \
    --grid-level standard -o build/ks-eq

# 或从已有 preview-2 JSON 起（推荐先 export --only-json，再多次试算）
uv run --all-extras python -m odbpp equivalent --from-json build/ks/PCB.json \
    --px 9600 --py 7200 --min-cell-mm 0.1 --max-cell-mm 5 -o build/ks-eq
```

`equivalent` 关键选项：

| 选项 | 作用 |
|---|---|
| `--px` / `--py` | 成对覆盖默认密集光栅；不足时明确报错 |
| `--grid-level` | coarse / standard / fine，默认 standard；尺寸随 PCB 几何自适应 |
| `--min-cell-mm` / `--max-cell-mm` | 单元边长上下限，单位 mm，各传一个值或 X Y 两个值，专家模式未指定的一项默认 0.1 / 5 mm；不能与显式档位混用 |
| `--materials FILE` | JSON 材料配置：`global`、`layer_types.copper/dielectric` 与 `layers.<name>` 覆盖（`copper`/`base` 各含 `k_w_mk`/`density_kg_m3`/`specific_heat_j_kgk`） |
| `--copper-k` / `--copper-density` / `--copper-cp` 与 `--base-*` | 逐相命令行覆盖 |
| `--no-raster-png` / `--raster-scale N` | 逐层黑白掩膜 PNG：默认自动取到约 1400 px 长边且**保持板的长宽比**；`N` 强制各向同性 N 倍放大（会拉伸非方形板） |
| `--no-heatmap` / `--heatmap-panel-width PX` / `--heatmap-shared-scale` | 逐层 `kx`/`ky`/`kz` 三面板热图；面板宽默认 1440 px；共享色标便于层间比较 |
| `--heatmap-grid` | 在热图中标出 CSV 单元边界；独立 `heatmap` 命令对应 `--grid` |
| `--no-gallery` | 不写 `<step>.images.html` 图例页 |

可视化产物只依赖 CSV，可以事后单独重画（不需要 ODB++ 输入，也不重算等效）：

```bash
uv run --all-extras python -m odbpp heatmap build/ks-eq/PCB.MaterialMap.csv \
    -o build/ks-eq/heatmaps --shared-scale --layer B.CU
```

掩膜三色语义：黑 = 铜/钻孔投影（材料 1），白 = 计算域内的基底（材料 2），
灰 = 该层在这里**没有计算域**（不是 FR4）。热图三面板共用一条色标，
浅灰 `n/a` 表示该层在此单元没有值。命令、色标、内存与失败处理详见
[可视化文档](docs/equivalence/02-visualization.md)。

输出 `*.MaterialMap.csv`（逐层逐单元 `kx/ky/kz/density/specific_heat`，并带
`cell_area_mm2`/`domain_area_mm2`/`domain_coverage` 明确计算域覆盖率）与同名
`*.MaterialMap.meta.json`（单位、Z 可信状态、材料来源、矩形化误差、诊断、
覆盖率语义）。矩形化不通过（填孔/丢岛/超精度）时视为致命，不写任何结果。
默认材料表、矩形化与网格规则、公式与验证见
[物性等效器文档](docs/equivalence/01-material-equivalence.md)。

`export` 产出的 HTML 是自包含的（CSS/JS/SVG 全部内联），可直接双击打开：
滚轮缩放、拖拽平移、逐层开关，以及 all / none / copper / isolate / invert /
wireframe 快捷按钮和实时毫米坐标读数。

导出性能测量、兼容性验证和当时发现的显示问题见
[2026-09-22 审阅报告](docs/reviews/05-export-performance-review.md)。
其中逐层 SVG 导出已复用整板视口，性能优化阶段 JSON/SVG/HTML 对四例样本与优化前逐字节一致。
解析、几何规划、材料采样和图像生成的后续优化与完整产物对照见
[2026-10-04 全流程性能报告](docs/reviews/06-pipeline-performance.md)。
上述显示问题及 profile 输出精度问题已修复，见
[修复与验证记录](docs/fixes/04-export-correctness-fixes.md)。

修改预览生成代码后，使用 `tools/sync_out.py --px 400 --py 400 --force` 更新已有产物；
仅修改等效或可视化代码时，用 `--force-equivalent`。同步结果按 case 放在
`out/<case>/`：预览 JSON/SVG/HTML、MaterialMap CSV/meta、`masks/`、
`heatmaps/` 及图例页，`out/index.html` 提供入口。
`out/index.html` 链接到四个既有样本及 KiCad 小型案例的原生、裁剪两组导出。
每份输入仅保留 JSON、合并 SVG、可交互 HTML；KiCad 输出分别位于
`out/kicad-positive/native/` 与 `out/kicad-positive/clean/`，避免混淆来源。


退出码：`0` 无错误，`1` 加载成功但存在 error 级诊断，`2` 直接失败。
**诊断是结果的一部分**——任何被丢弃或修补的数据都会以稳定 code 记录，
不会静默消失。

Python API：

```python
from odbpp import load_job, build_step_preview, write_outputs

job = load_job("cases/designodb-rigidflex.tgz")        # INCH，43 层
preview = build_step_preview(job)                      # 合成各层为正向多边形
write_outputs(preview, job, "build/rigidflex")         # JSON + SVG + HTML

top = preview.layer("SIGNAL_1")
print(top.area_mm2, len(top.rings), top.bounds)        # 单位一律 mm
for ring in top.rings:
    print(ring.is_hole, len(ring.points))              # 孔/岛是显式字段
```

只要解析结果、不做几何运算时：

```python
from odbpp import load_job

job = load_job("cases/minimal-test-odb")
step = job.single_step
for layer in step.layers:
    base = step.base_geometry(layer)                   # 该层的基底多边形
    print(layer.name, layer.type, layer.feature_count, len(base.polygons))
```

## 模块地图

| 模块 | 职责 |
|---|---|
| `archive.py` | 目录 / tar / zip 统一读取；不区分大小写的成员查找；按魔数识别成员内的 gzip |
| `units.py` | `UnitScope`：`coord_mm()` 与 `size_mm()` **分开命名**，无通用换算器；`copper_weight()` |
| `textio.py` | 行记录与结构化文本（`KEY=VALUE` / `BLOCK { }`）的词法、分段、类型化取值 |
| `model.py` | 数据模型：矩阵/层/step/多边形，以及唯一入口 `Step.base_geometry()` |
| `metadata.py` | `misc/info`、`matrix/matrix`、`stephdr`、`attrlist`、`sysattr*`、`tools` 的语义解析 |
| `features.py` | `features` / `profile` 文件：`P`/`L`/`A`/`S` 记录、`@`/`&` 两段间接寻址；`T`/`B` 明确不支持（约束 6） |
| `symbols.py` | 标准符号文法表（Appendix A）+ 自定义符号的惰性加载、递归展开、环检测 |
| `geometry.py` | 圆弧离散化、环方向约定、变换，以及 `r`/`s`/`rect`/`oval` 等基础标准符号的原生展开 |
| `job.py` | 编排：把上述内容组装成一个自洽的产品模型，并执行跨文件约束 |
| **`clipper.py`** | **平面布尔运算 + Minkowski 扫掠**：整数量化（1e-6 mm）、环方向归一、并/差/交、`stroke_paths`（圆符号精确带宽）、`sweep_paths`（方符号精确拖拽） |
| **`expand.py`** | **图元 → 实心区域**：符号解析与放置、`L`/`A` 扫掠、极性与基底合成、按极性段与 stroke 定义批处理；介质层扣除相交钻孔投影 |
| **`stackup.py`** | **层叠模型**：铜层序号、介质/钻孔的 Z 区间（半开）、相交判定，以及 `.layer_dielectric`/`.copper_weight` 的单位规范化 |
| **`export.py`** | **中间格式（数据流 2）**：逐层流式合成 `StepPreview`，输出公制 JSON（`odbpp/preview-2`，含逐层计算域） |
| **`svg.py`** | **渲染（数据流 3）**：SVG 与自包含 HTML 查看器 |
| **`rectify.py`** | **矩形化**：step profile → 轴对齐阶梯 region，记录面积/形状误差并逐岛逐孔校验 |
| **`grid.py`** | **整板网格**：region 边界强制格线，按单元尺寸上下限独立细分各强制区间 |
| **`raster.py`** | **光栅化**：region 边界扫描线 + even-odd 像素中心判定；`LayerMask` 同时供等效与掩膜 PNG 使用 |
| **`materials.py`** | **两相材料**：铜/基底默认值（带来源）、逐层 > 全局 > 默认的优先级与警告 |
| **`png.py`** | **图像编码**：零依赖的 8 位灰度/RGB PNG 写入器 + RGB 画布 + 5×7 点阵字体 |
| **`visualize.py`** | **等效结果可视化**：掩膜 PNG、`kx`/`ky`/`kz` 热图、图例页，并从已写出的 CSV 读取 |
| **`equiv.py`** | **物性等效（数据流 4-5）**：逐层光栅 + 钻孔覆盖 + 串并联公式，流式输出 CSV 与 meta |
| `cli.py` | `python -m odbpp` 子命令 |

## 几条不变式

实现中反复依赖、改动时最容易破坏的约定：

1. **单位永不隐式。** 坐标与「尺寸」（符号参数 / resize / 钻孔）是两套量纲，
   同数不同值（1000 倍）。所以 API 里没有 `to_mm`，只有 `coord_mm` 与 `size_mm`。
2. **下游一律是毫米。** 所有语义层的长度字段在进入模型时已换算完毕。
3. **矩阵是层的权威清单。** `layers/` 下未登记在矩阵的目录会被报告并忽略；
   反之矩阵有而磁盘无会告警。
4. **实体名查找不区分大小写。** 矩阵写 `F.CU`、目录是 `f.cu`，这在
   Linux 上才会炸。
5. **朝向是语义。** 岛顺时针、孔逆时针（规范 p.39）；`geometry` 生成的所有环
   都直接符合该约定，布尔阶段可直接信任。
6. **警告即数据。** 丢弃、回退、修补一律记录为带稳定 code 的 `Diagnostic`。
7. **能解析成标准符号的才算标准符号。** 判定靠文法，绝不靠前缀匹配
   （反例 `special_0.670x0.950_nt2520` 以 `s` 开头却不是方形）。
8. **绝不近似。** 走线/圆弧是符号沿路径的 Minkowski 扫掠（规范 p.38），
   不是"粗笔画"；两个正向特征叠加是并集，**不能**用 `fill-rule="evenodd"`
   了事（那会在叠加处挖孔）。为此引入真正的布尔运算，而不是渲染技巧。
9. **孔/岛是显式数据，不是推断出来的朝向。** 所有输出的环都按规范 p.39
   缠绕，但 JSON 里同时带 `island` 布尔字段——消费者不必知道约定就能正确读取。
10. **降级只降一层。** 某一层不可用（如约束 3 的 profile 失败）只让**该层**
    缺席预览，其余各层照常输出，并在诊断里区分 `empty_layers`（合成为空）
    与 `failed_layers`（无法合成）。
11. **Z 区间缺失就报错，不猜。** 钻孔的 `START_NAME`/`END_NAME` 解析不出来，
    就不扣除并记 `stackup.drill_span_unknown`；把未知跨度当成“整板贯穿”
    会在输出里造出与真实通孔无法区分的假孔。
12. **写出的坐标就是验证过的坐标。** 序列化前先把几何量化到输出网格并
    重新 union/Simplify，`area_mm2`、`bounds_mm` 均由最终坐标重算；
    在目标分辨率下无面积的环被丢弃并记 `export.degenerate_ring`。
13. **板外区域不是 FR4。** 等效器只接受 `odbpp/preview-2`；某层在某网格单元内
    是否存在，由其**逐层计算域**决定，绝不用铜图案反推。
14. **矩形化误差是显式数据。** 阶梯边界丢失/填平的面积、精度、以及每个岛/孔的
    存亡都写进 meta；超过精度允许的上界报 `rectify.area_error`，不静默近似。
15. **钻孔一律实心铜。** 与某层 Z 区间相交的钻孔投影在该层内按全铜处理
    （`drill_model=solid_copper`），铜层与介质层同理；跨度未知时报错，不猜通孔。
16. **网格保留边界并遵守尺寸约束。** 各方向单元边长必须在用户给定范围内，
    不兼容时报 `grid.size_incompatible`；不能删除 region 边界或悄悄放宽尺寸。
    每个单元必须完整落在一个 region 内，且至少有光栅像素中心。
17. **单元必有采样。** 网格单元内无像素中心时报 `grid.cell_without_pixel`
    （致命），不静默取 0 或最近像素。
18. **图不是数据。** 掩膜 PNG 取自等效计算所用光栅，热图从已写出的 CSV 重新读取渲染；
    板外不画成 FR4（灰 = 无计算域），`n/a` 不画成低热导率，也不拿显示效果判断收敛。

## 目录

| 路径 | 内容 |
|---|---|
| `cases/` | ODB++ v8.1 输入：4 个既有样本及 7 个 KiCad 小型导出（见 `cases/README.md`） |
| `docs/` | 官方规范 PDF（不入库）、`research/` 调研、`reviews/` 审阅、`fixes/` 修复记录、`equivalence/` 等效器文档与 `derivation/` 推导 |
| `external/` | 参考用开源项目（不入库，按需 clone，见 `external/README.md`） |
| `out/` | 与案例输入一一对应的导出结果及索引（不入库，由 `tools/sync_out.py` 维护） |
| `src/odbpp/` | 解析器本体 |
| `tests/` | 单元测试 + `tests/fixtures/` 合成样本 |
| `tools/` | 规范 PDF 获取/抽取、案例普查和 `out/` 同步 |

## 中间 JSON 携带的层叠与属性语义

`steps[].layers[]` 除多边形外还给出：`attribute_units`（该层 attrlist 的单位作用域）、
`thickness_mm` 与 `thickness{raw,value_mm,source}`（`.layer_dielectric` 的规范化厚度）、
`copper_weight{value,kind,unit}`（`MM` 为 µm 铜厚、`INCH` 为 oz/ft² 面密度）、
`copper_index`/`z_span`/`start_layer`/`end_layer`（层叠位置），以及
`drills_subtracted`/`drill_cut_mm2`（约束 3 实际扣除的钻孔与面积）。
`steps[].stackup[]` 按行序汇总整叠层。

## 测试

```bash
uv run --all-extras python -m pytest                       # 含真实样本
uv run --all-extras python -m pytest -k "not RealCases"    # 跳过真实样本
```

真实样本缺失时相关测试自动 skip（`conftest.case_path`），因此裁剪过
`cases/` 的检出仍可跑通全部合成测试。
