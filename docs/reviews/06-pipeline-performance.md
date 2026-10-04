# 06 · 全流程性能优化（2026-10-04）

基线源码：`adb64af`（业务实现与任务开始时的 `5b1ecf0` 相同）；最终优化源码：`899149e`。
复用已有四个真实 ODB++ 案例、官方 8.1 规范及开源调研。本轮保持接口、schema、默认参数、
离散化精度、极性、层域、region 策略、网格、Z、材料配置、诊断和串并联公式。

## 修改与缓存边界

1. **文本与归档索引**：无引号记录用 C 实现的字符串拆分与正则匹配，保留原 Token 列号，
   分隔符仍严格为 ASCII 空格/制表符；含引号记录继续走原来的校验逻辑。
   归档初始化一次建立直接子项索引，查询继续返回独立、排序后的列表，保持大小写与隐式目录行为。
2. **符号与实体展开**：标准符号是全部字段不可变的 frozen 模型，按名称和单位做最多 4096 项的
   LRU 缓存；非标准符号仍读取所在 job 的定义，冲突警告仍逐次执行。
   焊盘的原点实体只在一次极性段内复用，递归段有独立缓存，结束或异常时恢复之前的状态。
   返回实体不共享可变环列表；单独调用、下一次展开及修改公开符号几何后都会重新处理。
   圆弧展开直接使用坐标列表，省去随后立即丢弃的 Polygon/ContourPart；保留全部多边形先
   离散化、再规整的校验顺序和闭合点规则。
3. **几何规划**：预索引 profile 的 X 事件与可能跨越竖条的边；交点公式、阶梯边界、
   局部细化和拓扑检查不变。预计算同一原始域的侵蚀 core，各候选仍独立验证连通性和孔洞。
   core 与候选的交集只求一次；共同层域检查每个矩形只构造一次 Shapely box。
   包含竖条端点，以保留大坐标下向内探针舍入到端点时的原有判定。
4. **采样**：扫描边的上下界与差分只计算一次，水平边不进入活动集合；仍按原来的除法、乘法
   顺序求交点。一次等效内复用同一域的实体 region、域内网格及像素索引。
   行累计数组只覆盖该 band 需要的 X 范围，保持整数差分和原有逐行/逐列浮点公式。
   缓存不跨 JSON/光栅调用，几何分支不读取这些像素索引。
5. **图像**：NumPy 按原整数最近邻索引收集掩膜字节，取样规则和行方向完全一致。
   输出源行单调，只保留最近一行，替代保存所有已缩放源行的字典。
   三面板热图复用像素矩形位置、调色板三元组和渐变条；PNG 压缩参数和编码器不变。

基线 profiler 显示，刚挠板掩膜缩放约占 20.6 s，逐层采样约 10.6 s；
单独解析分析的约 96.5 百万次调用中，`split_sections` 与 `tokenize` 分别累计约 10.6 / 7.5 s。
这些包含 profiler 开销，仅用于定位，不与以下正常运行计时混用。

## 测量

Windows 11，Python 3.13.15；依赖版本、三次原始数据、产物长度与 SHA-256 保存在
[`performance-validation.json`](performance-validation.json)。每次重复启动新进程，
基线与优化版顺序运行，计时期间不并行跑测试或其他基准。
采用默认标准档位、自动密集光栅、逐层 SVG、掩膜 PNG 和默认 1440 px 热图。

下表为 load、compose、export、equivalent、masks、CSV、heatmaps 的**阶段中位数合计**，
不含解释器/导入启动、JSON 回读、metadata 序列化及产物指纹计算，不能直接等同于 CLI 总墙钟时间。

| 案例 | 优化前 / 后（s） | 阶段合计减少 | 掩膜输出提速 | 峰值工作集中位数前 / 后（MiB） |
|---|---:|---:|---:|---:|
| minimal-test | 1.853 / 0.756 | 59.2% | 29.6× | 81.5 / 82.8 |
| kitchen-sink | 3.205 / 1.439 | 55.1% | 18.4× | 87.0 / 89.3 |
| video | 27.608 / 19.669 | 28.8% | 19.5× | 364.0 / 361.1 |
| rigidflex | 117.360 / 93.982 | 19.9% | 19.3× | 1598.8 / 1592.2 |

刚挠板 load 从 14.552 s 降到 9.959 s，compose 从 68.590 s 降到 64.495 s，
equivalent 从 6.522 s 降到 4.900 s，掩膜输出从 13.703 s 降到 0.710 s。
剩余时间主要在大规模实体扫掠/布尔合成。本轮没有更换几何算法或降低精度。
小案例的索引和缓存有约 1–2 MiB 的额外常驻开销；较大案例峰值略降。
内存和毫秒级差异会受分配器、文件缓存与系统负载影响，不表示所有输入都同比改善。

## 行为核对

四个案例三次重复的 **192 个产物全部长度和 SHA-256 一致**：JSON、合并 SVG、HTML、
逐层 SVG、MaterialMap CSV、规整后的 metadata、掩膜和热图 PNG。
metadata 只排除源码指纹 `pipeline_fingerprint` 与输出位置 `source.path`；其余字段全部对照，
包括默认材料警告、钻孔覆盖、层域审计、拓扑、厚度可信状态和绝对 Z。

| 案例 | region 前 / 后 | 物性单元前 / 后 | 光栅前 / 后 |
|---|---:|---:|---|
| minimal-test | 1 / 1 | 8352 / 8352 | 4096 × 3277 |
| kitchen-sink | 78 / 78 | 12651 / 12651 | 4096 × 2595 |
| video | 6 / 6 | 24087 / 24087 | 4096 × 1401 |
| rigidflex | 45 / 45 | 66274 / 66274 | 4096 × 1089 |

新增独立参考测试覆盖：升/降采样及非整数比例、重复扫描行和顶点/水平边、
全 profile 与索引竖条求交、大坐标探针舍入、Token 列号与非 ASCII 空白、
符号单位隔离与参数视图、带圆弧和重复闭合点的几何转换、焊盘缓存退出后的修改、归档索引列表隔离。

全量回归：**737 passed in 197.16 s**；优化前为 717 passed in 242.23 s。
新增 20 项回归全部通过。`--source-ref` 的独立基线重放也通过产物指纹对照。

`out/` 的 18 个案例全部同步完成；四个大样本采用本次已对照通过的导出，14 个 KiCad 输入
重新执行 CLI 预览导出，刷新之前留存的旧版 HTML，同时重新生成 CSV、metadata、
掩膜、热图、图例、region/网格诊断图及索引。逐单元审计 **320664 行**全部通过，覆盖域内
包含、region 归属、共同轴线、尺寸约束、采样容量、完整体积、Z 和物性范围，详见
[`performance-output-validation.json`](performance-output-validation.json)。

另外对刚挠板与 kitchen-sink 分别使用现有默认分辨率、两轴像素数各加倍、64 × 64：
每种分辨率均在独立进程中对照 Git 基线和优化版。**未舍入的五项浮点物性、整层掩膜字节、
metadata 和失败诊断完全一致**；三种分辨率的 region/网格几何指纹相同，64 × 64 仍明确失败。
默认与加倍分辨率之间的材料采样差异及每次耗时见
[`performance-sampling-validation.json`](performance-sampling-validation.json)。
加倍分辨率只用作此次差异参照，并非物理真值或已证明收敛的极限；几何近似损失仍独立报告。

默认与加倍光栅的单元相对变化 p95 如下，两版数值相同。少数单元的最大相对变化更高，
完整统计在上述 JSON 中。这说明输出等价验证不能替代具体板型的采样收敛判断。

| 案例 | kx | ky | kz | 密度 | 比热 |
|---|---:|---:|---:|---:|---:|
| rigidflex | 12.36% | 11.53% | 19.95% | 2.98% | 1.88% |
| kitchen-sink | 1.15% | 1.06% | 0.88% | 0.47% | 0.30% |

## 复现

```powershell
uv sync --all-extras
uv run --all-extras python tools/benchmark_pipeline.py --source-ref adb64af --output build/perf-before --repeat 3
uv run --all-extras python tools/benchmark_pipeline.py --output build/perf-after --repeat 3 --compare build/perf-before/benchmark.json
uv run --all-extras python -m pytest
uv run --all-extras python tools/sync_out.py --force-equivalent
uv run --all-extras python tools/validate_architecture.py --audit-out --output build/perf-output-audit.json
uv run --all-extras python tools/validate_performance_sampling.py --baseline-ref adb64af
```

`--source-ref` 仅保存指定提交的包源码，不切换或覆盖当前工作区；基准通过进程的模块搜索路径
选择源码。`--compare` 只统计本次 API 返回的实际产物，旧目录中的遗留文件不能掩盖输出缺失。
要单独分析热点，增加 `--profile --repeat 1`，使用另一个输出目录；不要把 profiler 数字当作正式基准。

正式 `out/<case>/` 保留同步后的最终版本；任务产生的重复基准产物和临时源码在验证后清理。
