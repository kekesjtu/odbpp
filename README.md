# odbpp

ODB++ v8.1 → 统一的公制多边形模型（JSON）/ 分层矢量预览（SVG）。

当前状态：**解析器骨架可用**。可以读取目录、`.tgz`/`.tar.gz`、`.zip` 形式的
ODB++ job，解析矩阵、层、图元、钻孔、自定义符号，并把结果整理成公制（mm）的
模型对象。几何布尔运算与 SVG 输出尚未实现。

```
[ ODB++ 压缩包/目录 ]
        │
        ▼
[ 1. ODB++ Parser ]  ← 已实现
        ├──────────────────────────────────┐
        ▼                                  ▼
[ 2. 中间格式 (.json) ]            [ 3. 分层矢量预览 (.svg) ]
        (待实现)                          (待实现)
```

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
```

退出码：`0` 无错误，`1` 加载成功但存在 error 级诊断，`2` 直接失败。
**诊断是结果的一部分**——任何被丢弃或修补的数据都会以稳定 code 记录，
不会静默消失。

Python API：

```python
from odbpp import load_job

job = load_job("cases/designodb-rigidflex.tgz")   # INCH，43 层
step = next(iter(job.steps.values()))
for layer in step.layers:
    base = step.base_geometry(layer)               # 该层的基底多边形
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
| `features.py` | `features` / `profile` 文件：`P`/`L`/`A`/`S`/`T`/`B` 记录、`@`/`&` 两段间接寻址 |
| `symbols.py` | 标准符号文法表（Appendix A）+ 自定义符号的惰性加载、递归展开、环检测 |
| `geometry.py` | 圆弧离散化、环方向约定、变换，以及 `r`/`s`/`rect`/`oval` 等基础标准符号的原生展开 |
| `job.py` | 编排：把上述内容组装成一个自洽的产品模型，并执行跨文件约束 |
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
python -m pytest              # 270 项，含真实样本（rigidflex 约需 25s）
python -m pytest -k "not RealCases"   # 跳过真实样本，约 1s
```

真实样本缺失时相关测试自动 skip（`conftest.case_path`），因此裁剪过
`cases/` 的检出仍可跑通全部合成测试。
