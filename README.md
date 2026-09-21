# odbpp

ODB++ v8.1 → 统一的公制多边形模型（JSON）/ 分层矢量预览（SVG）。

当前状态：**三阶段数据流全部可用**。读取目录、`.tgz`/`.tar.gz`、`.zip` 形式的
ODB++ v8.1 job，展开图元与符号，用布尔运算合成**完全正向**的公制多边形，
输出中间 JSON 与带图层开关、缩放平移的 HTML/SVG 预览。

```
[ ODB++ 压缩包/目录 ]
        │
        ▼
[ 1. ODB++ Parser ]  ← 已实现  load_job()
        ├──────────────────────────────────┐
        ▼                                  ▼
[ 2. 中间格式 (.json) ]            [ 3. 分层矢量预览 (.svg/.html) ]
  已实现  build_step_preview()        已实现  render_svg() / render_html()
          step_to_json()                        write_outputs()
```

四个真实样本均已跑通，含 520k 图元 / 43 层的 INCH 刚挠结合板
（导出约 62 s，排除 `DOCUMENT` 层）。

## 快速开始

```bash
conda activate odbpp
pip install -e .

# 结构总览（层清单、图元类型统计、诊断）
python -m odbpp summarize cases/kicad-kitchen-sink

# 机器可读的结构转储
python -m odbpp summarize cases/kicad-kitchen-sink --format json --json out/ks.json

# 其它子命令
python -m odbpp info        <path>          # job root / 单位 / ODB++ 版本
python -m odbpp layers      <path> [--step S]
python -m odbpp symbols     <path>
python -m odbpp diagnostics <path>

# 导出预览：<out>/<step>.json / .svg / .html
python -m odbpp export cases/kicad-video-odb.tgz -o out/video
python -m odbpp export cases/designodb-rigidflex.tgz --exclude-type DOCUMENT                       --per-layer-svg --minimum-stroke 0.05
python -m odbpp export <path> --layer top --layer bottom --only-json
```

`export` 的主要选项：

| 选项 | 作用 |
|---|---|
| `--layer NAME` / `--exclude-type TYPE` | 选择要合成的层（可重复） |
| `--sagitta MM` | 圆弧离散化容差，默认 0.005 mm |
| `--digits N` | 坐标小数位，默认 4（0.1 µm） |
| `--minimum-stroke MM` | 把零宽（`r0`）图元按发丝线绘制；默认 0，即几何精确因而不可见 |
| `--only-json` / `--per-layer-svg` | 只要 JSON / 额外每层一个 SVG |

`export` 产出的 HTML 是自包含的（CSS/JS/SVG 全部内联），可直接双击打开：
滚轮缩放、拖拽平移、逐层开关，以及 all / none / copper / isolate / invert /
wireframe 快捷按钮和实时毫米坐标读数。


退出码：`0` 无错误，`1` 加载成功但存在 error 级诊断，`2` 直接失败。
**诊断是结果的一部分**——任何被丢弃或修补的数据都会以稳定 code 记录，
不会静默消失。

Python API：

```python
from odbpp import load_job, build_step_preview, write_outputs

job = load_job("cases/designodb-rigidflex.tgz")        # INCH，43 层
preview = build_step_preview(job)                      # 合成各层为正向多边形
write_outputs(preview, job, "out/rigidflex")           # JSON + SVG + HTML

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
| **`expand.py`** | **图元 → 实心区域**：符号解析与放置、`L`/`A` 扫掠、极性与基底合成、按极性段与 stroke 定义批处理 |
| **`export.py`** | **中间格式（数据流 2）**：逐层流式合成 `StepPreview`，输出公制 JSON |
| **`svg.py`** | **渲染（数据流 3）**：SVG 与自包含 HTML 查看器 |
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

## 目录

| 路径 | 内容 |
|---|---|
| `cases/` | ODB++ v8.1 真实样本（4 例，见 `cases/README.md`） |
| `docs/` | 官方规范 PDF（不入库）+ `docs/research/` 调研报告 |
| `external/` | 参考用开源项目（不入库，按需 clone，见 `external/README.md`） |
| `out/` | 导出结果（不入库） |
| `src/odbpp/` | 解析器本体 |
| `tests/` | 单元测试 + `tests/fixtures/` 合成样本 |
| `tools/` | 规范 PDF 抽取/校验、case 普查 |

## 测试

```bash
python -m pytest                       # 461 项，含真实样本（约 3 分钟）
python -m pytest -k "not RealCases"    # 跳过真实样本，约 40 s
```

真实样本缺失时相关测试自动 skip（`conftest.case_path`），因此裁剪过
`cases/` 的检出仍可跑通全部合成测试。
