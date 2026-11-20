# 测试指南

在仓库根目录运行以下命令。使用 `uv sync --extra test` 安装 Python 测试依赖并构建解析器扩展。

运行等效计算、CLI 管道或整个 pytest 测试集前，先构建 `pcbeq` 并指定可执行文件。以下命令适用于 Windows PowerShell。

```powershell
uv sync --extra test
cargo build -p pcbeq-rust --release
$env:PCBEQ_EXE = (Resolve-Path target/release/pcbeq.exe).Path
```

Linux / macOS 下使用 `export PCBEQ_EXE="$PWD/target/release/pcbeq"`。大部分用例在测试中生成小型 ODB++ 或中间 JSON。使用本地 `cases/minimal-test-odb` 的 CLI 用例在样本缺失时会跳过。

## 测试范围与定向入口

| 范围 | 覆盖内容 | 从仓库根目录运行 |
| --- | --- | --- |
| ODB++ 解析 | 输入包装、压缩、单位、叠层、极性、符号与预览 | `uv run --extra test pytest tests/rust/test_parser_inputs.py tests/rust/test_parser_semantics.py tests/rust/test_pcbkit_parser.py` |
| 跨产品几何 | 板与层的轮廓、孔洞、有效域、钻孔跨度与拓扑 | `uv run --extra test pytest tests/rust/test_layer_profiles.py tests/rust/test_drill_topology.py` |
| 等效计算与发布 | 几何规划、采样密度、材料优先级、各向异性、诊断、图表与失败回滚 | `uv run --extra test pytest tests/rust/test_fast_planning.py tests/rust/test_native_pipeline.py tests/rust/test_diagnostics.py` |
| 数值与产品边界 | 圆弧端点、坐标有效性、两产品的运行环境 | `uv run --extra test pytest tests/rust/test_numerical.py` |
| CLI 和案例同步 | 独立可执行文件、CLI 调用、案例管道、缓存复用与单案例同步 | `uv run --extra test pytest tests/rust/test_rust_cli.py` |
| 分发与契约 | 包元信息、导入边界、中间 JSON 模型副本与等效输入边界 | `uv run --extra test pytest tests/distribution` |
| Rust 几何单元测试 | 多边形布尔运算与曲线离散 | `cargo test -p pcb-geometry` |
| Rust 等效单元测试 | 分区、网格、采样、串并联物性及数值产物发布 | `cargo test -p pcbeq-rust` |

`pytest` 可用 `-k` 缩小到相关行为。默认临时目录为 `tmp/pytest-tmp`。需要按任务保留测试产物时，可将 `--basetemp` 指向任务工作区中的 `pytest/` 子目录。

```powershell
$taskScratch = 'tmp/YYYY-MM-DD-case-sync'
New-Item -ItemType Directory -Force -Path $taskScratch | Out-Null
uv run --extra test pytest tests/rust/test_rust_cli.py -k sync_out --basetemp "$taskScratch/pytest"
```

把路径中的 `YYYY-MM-DD-case-sync` 换成本次任务的临时目录名。改动中间 JSON 契约时，运行 `uv run python tools/sync_contract.py --check` 检查模型副本及 schema。

## 全量验证

```powershell
uv run --extra test pytest
cargo test --workspace
```

需要验证单独安装、交付和 sdist 重建时，使用[分发验证工具](../tools/README.md)。
