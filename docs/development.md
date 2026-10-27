# 开发、运行与验证

## 环境

在仓库根目录使用 uv 管理 `.venv/`，解释器版本为 `.python-version` 中的 Python 3.13，依赖由 `uv.lock` 固定。

```powershell
uv sync --all-extras
```

添加依赖使用 `uv add`；依赖声明变更后审核并提交锁文件。如果本机共享 uv 缓存不可写，可以在当前终端设置项目内缓存：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD 'build/uv-cache'
uv sync --all-extras
```

## 两个独立 CLI

```powershell
uv run --all-extras pcbkit export cases/minimal-test-odb -o out/example/pcbkit --sagitta-mm 0.001 --digits 4
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --grid-level standard
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --px 4096 --py 4096 --max-cell-mm 2 3
```

`--sagitta-mm` 控制曲线弦高误差，`--digits` 控制最终毫米坐标分辨率。
`--only-json` 只导出中间 JSON，`--per-layer-svg` 额外输出逐层预览。
`pcbkit info/layers/symbols/diagnostics/summarize` 用于输入检查，参数以各子命令 `--help` 为准。

`pcbeq` 的 `--px/--py` 成对提供，分别表示整板两个方向的像素数。
常规网格使用 `--grid-level coarse|standard|fine`，默认 standard；专家使用 `--max-cell-mm` 的一个值或 X/Y 两值，与显式档位互斥。
region 精度使用 `--max-boundary-retreat-mm`（默认 3 mm）和 `--max-area-loss-ratio`（默认 0.05）。
材料由 `--materials FILE` 或六个 `--copper-*` / `--base-*` 参数配置，字段与来源见 [材料模型](decisions/material-model.md)。

## 协同管道与案例输出

```powershell
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/sync_out.py --force-equivalent
uv run --all-extras python tools/audit_out.py
```

单案例管道通过两个实际子进程交接 JSON，可用 `--pcbkit-exe` 与 `--pcbeq-exe` 指定独立环境的命令。
批量同步默认复用符合输入、实现指纹和请求配置的完整产物；`--force` 重建两个阶段，`--force-equivalent` 重算等效阶段。
案例资料位于本地 `cases/`；合成测试不依赖下载案例。产物目录及成功发布规则见 [输出与发布](decisions/output-publication.md)。

## 契约维护

```powershell
uv run --all-extras python tools/sync_contract.py
uv run --all-extras python tools/sync_contract.py --check
```

权威源为 `contracts/intermediate.py`，同步目标包括两个包的 `intermediate.py`、`contracts/intermediate.schema` 和 `docs/interfaces/intermediate.schema`。
格式变更同步调用方、接口说明及案例输出，字段与必要语义由 Pydantic 校验。

## 按变更选择验证

```powershell
uv run --all-extras pytest
uv run --all-extras pytest tests/contracts tests/distribution
uv run --all-extras pytest packages/pcbkit/tests
uv run --all-extras pytest packages/pcbeq/tests
uv run --all-extras pytest tests/integration
```

契约或分发路径变更运行跨包边界检查；算法变更运行相应包内与集成测试。
涉及真实几何、材料和输出的变更同步案例并运行独立审计。完整验收项目见 [需求](../requirements.md#验证要求)。

几何精度和材料采样分别测量：

```powershell
uv run --all-extras python tools/validate_region_accuracy.py --output docs/validation/reports/region-accuracy.json
uv run --all-extras python tools/validate_region_accuracy.py --case kicad-video-odb.tgz --sampling-convergence --output docs/validation/reports/region-sampling.json
uv run --all-extras python tools/benchmark_pipeline.py --case minimal-test-odb --repeat 2 --output build/benchmark
```

几何报告对照两种 Px/Py 下的完整 region / 全局轴线坐标，材料收敛需要实际采样对照。
`tracemalloc` 的 Python 分配峰值与进程原生峰值分别记录；不同计时范围单独比较。
仅 CSS/JS 变化可用 `tools/refresh_viewer_outputs.py` 更新预览；几何、标记或元数据变化使用完整同步。

## 构建与独立安装

```powershell
uv build --package pcbkit --out-dir build/dist
uv build --package pcbeq --out-dir build/dist
uv run --all-extras python tools/verify_distributions.py
```

分发验证在独立虚拟环境中分别安装两产品，从 sdist 重建 wheel，并检查等效器在原始 ODB++ 输入移走后仍能消费 JSON。
依赖已缓存时可以加 `--offline`。详细工具参数见 [工具索引](../tools/README.md)。

## 临时文件与证据

测试、构建和基准过程产物使用 `build/`，正式结果使用 `out/<case>/`。
关闭占用进程后，仅清理确认无用的临时文件。验证报告放在 [validation](validation/README.md)，原始文档快照放在 [archive](archive/README.md)。
