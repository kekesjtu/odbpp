# Region 精度：轮廓距离与面积损失

2026-10-27。当前策略为 `inscribed-vertical-staircase-worst-first`，替代旧 v3 的面积预算驱动策略。

## 两个用户参数

```powershell
uv run --all-extras pcbeq run out/kicad-video-odb.tgz/pcbkit/intermediate.json -o out/kicad-video-odb.tgz/pcbeq --max-boundary-retreat-mm 0.5 --max-area-loss-ratio 0.05
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--max-boundary-retreat-mm` | 0.5 mm | 原始实体与内接模型对应轮廓之间的双向欧氏距离上限，有限正数 |
| `--max-area-loss-ratio` | 0.05 | 损失面积 / 原始有效域面积的上限，范围 `[0,1)`，0.01 表示 1% |

两者同时约束 step profile 和每个待等效层的有效域（layer profile AND 原始 step profile）。
不再用绝对面积下限放宽小板的比例预算。零损失可用于精确轴对齐板型；有限轴对齐矩形不能精确表达真正的斜边，零损失时明确失败。

`tools/run_pipeline.py` 和 `tools/sync_out.py` 也接收并传递这两个参数；批量缓存把两者纳入请求指纹。
原来的隐藏面积比例/绝对面积命令行参数退役。隐藏 `--diagnostic-geometry-step-mm` 只覆盖内部初始种子尺度，不能代替误差预算。

## 距离检查的几何含义

先对实际矩形集合求并集，再与原始有效域比较，因此矩形间的内部接缝不算轮廓。
外轮廓按所属实体一一对应，profile 孔洞按包含关系一一对应；不能让某个孔的边界错误地匹配到附近板边。
每对轮廓都检查原始 → 模型、模型 → 原始两个方向。DRILL 不参加轮廓规划，它仍是介质采样中的实心铜图案。

检查完整线段是否被对方轮廓的距离管带覆盖，而非只检查顶点或 Y 向退缩。
圆角管带使用内接多边形近似（每象限 32 段），因此通过覆盖检查给出保守的连续距离保证；圆角近似可能多拒绝少量临界候选，不会放宽给定预算。
GEOS 的离散 Hausdorff 值只用作下界和快速拒绝，不能据此接受候选。
最终距离上界通过管带半径二分得到，二分半径分辨率为 `max(1e-9 mm, ε × 0.001)`。
输出的下界仍是顶点距离下界，并不宣称上下界间隔都达到该分辨率。

## 最差位置优先细化

1. 用物理尺寸建立竖条种子；自动初始尺度为 `min(max(0.5 mm, 整板短边 / 32), ε / 2)`。
   同一 step 的层域共用 X 基础和 Y 原点。轴对齐轮廓按真实 X 顶点精确分条。
   初始尺度只是降低迭代成本的种子，不能作为误差合格的依据。
2. 每轮重建内接矩形的并集，检查真实外轮廓和孔洞边界。
3. 距离超差时，通过接近最大偏差的连续管带残余片段定位最差位置，优先二分其所属竖条；数值定位精度与距离二分分辨率一致。
   同一位置落在相邻条带公共边时，优先选损失面积较大的相邻条带。残余片段必须分别处理，禁止在不相连片段间生成虚假的中点。
4. 距离通过后，如果总面积仍超预算，优先细化损失贡献最大的条带。
5. 面积和距离都通过后继续检查岛、孔、正宽连接和内部保护核。拓扑失败时优先修复没有建模实体的条带；其余按损失贡献细化。

切片的人为截面边不参与轮廓距离。仅计算单个切片轮廓会在陡边附近产生假超差，甚至导致无限缩小条带而无法解决真正误差。
每轮的原因、选中区间、最差位置、距离上界和面积损失写入 `audit.local_refinement`。

## 合并与逐层共享分区

先合并上下截面完全一致的相邻竖条，再尝试统一 Y 基础、取相邻条带共同截面。
目标仍是减少传播至全板的 X/Y 强制轴线，其次减少 region 数。
任何会改变实体的候选都重查面积、连续距离、内接与拓扑；批量不通过时逐个检查，不能消费面积预算来突破局部距离约束。
小规模距离管带缓存以完整不可变几何与物理半径为键，不依赖光栅。

step / layer 模型相交并共享分区后，对每个原始有效域再次完整检查两预算与拓扑。
失败给出具体 domain 和超差数据，不生成可被误认为成功的 MaterialMap。
当前算法是受约束的启发式，不承诺最少矩形数，也不承诺在所有可解的多域问题上都找到方案。

## 网格、光栅与终止限制

region 的所有边界继续传播到全局笛卡尔栅；物性网格尺寸按各相邻强制区间独立等分。
`Px/Py` 只影响随后材料采样。收紧几何误差会增加强制轴线和单元数，也可能触发默认光栅的容量上限。
专家最小单元尺寸与强制边界冲突、显式光栅不足均报错，不回头修改几何。

内部资源限制：初始条带最多 10,000 条；逐次二分最多 10,000 次；单条带最多二分 20 层；
最小条带宽度与 Y 量化尺度为 `1e-5 mm`。这些是资源终止条件，并非可保证保留任意微小特征的物理精度。
到达限制仍不合格时，诊断含 X 区间、长度、当前尺度、距离预算及迭代限制。
内接面积检查继续采用 `1e-8 mm²` 数值容差，不能把它用作物理面积损失预算。

## 输出与验证

`diagnostics/run.json` 和 `regions/regions.stats.json` 保留两预算、连续距离上界、逐次细化历史、
合并拒绝原因、每域共享分区后的复检结果。region 图显示距离预算、实际距离上界和面积损失。

```powershell
uv run --all-extras pytest
uv run --all-extras python tools/validate_region_accuracy.py
uv run --all-extras python tools/validate_region_accuracy.py --case kicad-video-odb.tgz --max-boundary-retreat-mm 0.25 --max-area-loss-ratio 0.01 --diagnostic-geometry-step-mm 1 --output docs/equivalence/region-accuracy-tight-validation.json
uv run --all-extras python tools/validate_region_accuracy.py --case kicad-video-odb.tgz --sampling-convergence --output docs/equivalence/region-sampling-validation.json
uv run --all-extras python tools/sync_out.py --force-equivalent
uv run --all-extras python tools/audit_out.py
```

默认真实案例对照见 [region-accuracy-validation.json](region-accuracy-validation.json)，收紧预算见
[region-accuracy-tight-validation.json](region-accuracy-tight-validation.json)。默认对照同时逐一核对 4096² 和 8192² 两种光栅输入下的 region、全局 X/Y 轴线完全一致；该项只运行几何规划，材料采样容量由完整 CLI 和逐单元输出审计另行验证。
耗时包含几何规划和网格生成，前后均开启 tracemalloc；Python 分配峰值不包含 GEOS/NumPy 的原生内存，不应误读为进程峰值。

默认策略的初始种子已经可能满足两项预算，所以三种真实案例默认运行的细化次数均为 0；之后仍对每次简化复检。
收紧预算的 video 验证专门覆盖较粗的内部种子，实际执行 391 次最差位置细化，最终距离上界 0.249608 mm、面积损失 0.347992%。该内部种子覆盖仅为验证细化路径，不是第三个用户精度参数。

材料采样对照见 [region-sampling-validation.json](region-sampling-validation.json)。
video 默认 5621×1921 与双倍 11242×3842 的 35,861 个完整三维单元坐标全部相同；
kx/ky 的第 95 百分位相对变化约为 12.34% / 12.10%。细走线和接近纯 FR4 的单元仍有很大的相对差异，
因此该对照验证了几何独立性并量化采样变化，不能宣称所有单元的物性都已收敛。几何距离预算不承担材料采样误差保证。

## 本次真实案例结果

旧策略基线提交为 `6e66c70`；新策略采用 0.5 mm / 5% 默认预算。下表误差为 step profile，逐层域检查结果见 JSON 报告。

| case | region 前→后 | XY 单元前→后 | 距离上界前→后 mm | 面积损失前→后 | 几何与网格秒前→后 | Python 分配峰值 MiB 前→后 |
|---|---:|---:|---:|---:|---:|---:|
| kitchen-sink | 78→566 | 4,217→129,818 | 4.04113→0.5 | 4.9971%→0.6895% | 1.212→39.641 | 7.72→42.49 |
| video | 6→26 | 3,441→5,123 | 13.34764→0.471983 | 4.9267%→0.3868% | 0.785→1.403 | 0.97→2.07 |
| 刚挠板 | 45→127 | 4,262→10,443 | 2.25419→0.5 | 4.9851%→1.9655% | 1.948→12.093 | 2.45→3.90 |

更严格的局部距离约束明显增加 kitchen-sink 的轴线和计算量；当前策略不以牺牲误差预算换取旧版规模。
这些是本机单次测量，运行期间存在其他验证进程，不应作为性能承诺。
19 个案例的正式 MaterialMap、掩膜、热图和几何图已更新到 `out/`，
逐单元审计见 [output-validation.json](output-validation.json)。
本轮完整回归 **817 项通过**（260.58 秒）；19 个案例共 **934,041 个三维物性单元**全部通过输出审计。
新旧误差、耗时与 Python 分配峰值均保留在默认对照报告；新产物的 `published_run` 另记录含图像与 IO 的完整 CLI 耗时和原生进程峰值，不能与仅几何规划的测量混用。
