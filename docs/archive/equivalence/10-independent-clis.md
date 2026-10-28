# pcbkit 与 pcbeq 独立 CLI

2026-10-24。当前命令、文件架构和分发方式以本文为准；早期文档中的 `odbpp` 命令和扁平输出目录已经退役。算法、材料默认值与物理语义继续沿用现有推导。

## 软件边界

仓库包含两个独立 Python 分发项目：`packages/pcbkit`、`packages/pcbeq`。各自有 pyproject、README、src、tests 和命令入口，可分别构建 wheel/sdist。根 pyproject 是不发布的 uv 开发工作区，引用两个本地成员，统一管理 uv.lock 和测试依赖。

`pcbkit` 读取 ODB++ 目录、tar 或 zip，完成单位解释、符号展开、极性合成、拓扑规整、域选择及绝对 Z 输出，交付中间 JSON 和 SVG/HTML。其运行依赖为 Pydantic、pyclipper，不需要安装 NumPy、Shapely 或 Matplotlib。

`pcbeq` 的板数据输入仅为自己的 intermediate.py 所定义的 JSON。不读取原始 ODB++、不访问 JSON source 记录中的路径、不调用 pcbkit，也不提供 `--from-json` 或直接读取 MaterialMap 的另一条业务 CLI 路径。材料配置和命令行精度参数属于配置输入。物性计算仍保留两条独立分支：域与拓扑驱动 region/grid，JSON 材料图案驱动光栅；光栅不能影响几何规划。

两侧不互相 import。等效器的 geometry.py 从原有多边形核中保留直线环、整数量化及必要 profile 布尔操作，去掉解析器的曲线、SurfaceGeometry 和符号适配。复杂材料图案仍通过独立扫描线/掩膜组合。

## 唯一契约

权威定义为 `contracts/intermediate.py`。`tools/sync_contract.py` 同步两侧包内的 intermediate.py，并生成 contracts/intermediate.schema 和 docs/intermediate.schema。生成文件提交到 Git，两个分发包自带模型，构建时无需访问上级 contracts 目录。

```powershell
uv run --all-extras python tools/sync_contract.py
uv run --all-extras python tools/sync_contract.py --check
```

禁止分别手工编辑两份模型。CI/pytest 检查源码和 schema 的一致性。只保留一套当前 JSON 格式，不增加格式版本校验或兼容迁移；软件版本仅用于分发。Python 调用方也应在两侧边界显式序列化或读取 JSON，不能把 pcbkit 的模型对象直接传给 pcbeq。

## 命令

```powershell
uv sync --all-extras
uv run --all-extras pcbkit export cases/minimal-test-odb -o out/example/pcbkit --sagitta-mm 0.001 --digits 4
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --px 4096 --py 4096 --grid-level standard
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --max-cell-mm 2 3
```

pcbkit 保留 info/layers/symbols/diagnostics/summarize 供 ODB++ 检查使用。`--sagitta-mm` 控制曲线弦高误差，`--digits` 控制最终坐标量化；原 `--sagitta` 是同一参数的别名。`--only-json` 跳过预览图。

pcbeq 保留粗/标准/细档位和专家尺寸的互斥规则；光栅默认 4096×4096 起步，几何确定后可自动加密。显式 Px/Py 不足时仍报错。默认生成 masks、heatmaps、regions、grids；`--no-raster-png`、`--no-heatmap`、`--no-regions`、`--no-grids`、`--no-gallery` 可分别关闭。region 的物理尺度及误差预算仍属于内部策略。

## 输出与发布

```text
out/<case>/
  pcbkit/
    intermediate.json
    preview.svg
    preview.html
    diagnostics/run.json
    diagnostics/artifacts.json
  pcbeq/
    materialmap.json
    masks/*.png
    heatmaps/*.png
    regions/regions.png, regions.svg, regions.csv, regions.stats.json
    grids/cartesian-grid.png, cartesian-grid.svg, cartesian-grid.stats.json
    index.html
    diagnostics/run.json
    diagnostics/artifacts.json
  pipeline/
    run.json
    pcbkit.stdout.log, pcbkit.stderr.log
    pcbeq.stdout.log, pcbeq.stderr.log
```

MaterialMap 的物理语义和 region/单元结构不变，仅统一正式文件名为 materialmap.json。根诊断不回流到中间 JSON。运行报告保留精度、几何/采样诊断、材料来源和 CLI 进程内峰值内存；各阶段总墙钟时间由管道记录。

两侧先在同级临时目录计算和绘图，成功后发布。artifacts.json 只跟踪本软件生成的文件，后续运行清理其中已退役的产物，保留未登记的用户文件。发布错误执行回滚。pcbeq 请求的绘图失败视为运行失败，上一轮结果不被替换；pcbkit 允许发布带显式 failed 层状态的部分预览并返回 1，管道会停止，不调用下游。

掩膜来自实际材料采样；热图从正式写出的 MaterialMap 读取；region/grid 图使用本次实际规划或最终 JSON 还原的几何，不再次规划。区域表记录编号和尺寸，几何图与物性热图分别存放。

## 进程管道与批量运行

```powershell
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example --px 4096 --py 4096 --grid-level standard
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/sync_out.py --force-equivalent
uv run --all-extras python tools/audit_out.py
```

run_pipeline.py 只依赖标准库，用 subprocess 和参数列表调用两个实际命令，禁止导入计算模块。`--pcbkit-exe`、`--pcbeq-exe` 支持分别指定不同虚拟环境的入口。日志、返回码、耗时及交接 JSON 的 SHA-256 写入 pipeline/run.json；上游非零立即停止，旧的下游产物不能让本轮变成成功。

sync_out.py 复用同一管道，通过实现指纹、输入修改时间、JSON 内容哈希和用户配置判断是否可复用；缓存不参与几何计算。更改 Px/Py 只重算等效阶段，不改变 region/grid。旧的 tools/plot_regions.py、tools/plot_grid.py 已退役，绘图现在随 pcbeq 分发。

benchmark_pipeline.py 也通过进程管道测量两侧总耗时、实际进程的峰值内存、region/网格数；重复运行比较 JSON 和材料掩膜的哈希。旧的内核分段计时、源码快照和进程内 profiler 参数不再代表这条 CLI 管道。

## 独立分发与验收

```powershell
uv build --package pcbkit --out-dir build/dist
uv build --package pcbeq --out-dir build/dist
uv run --all-extras python tools/verify_distributions.py
uv run --all-extras python -m pytest
```

verify_distributions.py 在 build/distribution-check 中构建 wheel/sdist，再从 sdist 单独重建 wheel。两个干净虚拟环境分别安装一个产品及其依赖，确认另一产品不可导入、解析器环境无需数值/绘图库；复制真实 ODB++ 后由解析器导出，再移除复制的 ODB++ 输入，确认等效器仍完成计算和所有图片。最后用两套环境的命令入口运行整条管道。依赖已缓存时可以加 --offline。

测试分布：两包 tests 放算法和 CLI 回归；tests/contracts 检查接口，tests/integration 检查真实数据与跨进程行为，tests/distribution 检查依赖和导入边界。项目根 conftest.py 提供统一样本路径。测试不使用根 src/odbpp 兼容壳。

本次验证见 `independent-clis-validation.json`：完整回归 800 项通过，日志发布调整后的相关回归 45 项通过，19 个真实 case 的中间几何、region、全局轴线、MaterialMap 解码内容及材料掩膜均与旧结果相同；旧文件的 JSON 排版差异单独记录。最终 case 输出已迁入 pcbkit/pcbeq/pipeline 子目录，旧产物归档到 build/refactor-baseline/legacy-out。
