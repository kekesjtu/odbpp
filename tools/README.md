# 维护工具

从仓库根目录使用 `uv run python tools/<脚本> --help` 查看输入和参数。

| 工具 | 用途 |
|---|---|
| `run_pipeline.py` | 通过两个独立 CLI 串联 ODB++ → JSON → MaterialMap |
| `sync_out.py` | 同步本产品线案例、缓存与图库 |
| `sync_contract.py` | 同步本分支中间 JSON 模型与 schema |
| `verify_distributions.py` | 独立安装与 sdist 重建验证 |
| `benchmark_pcbeq.py` | 测量从中间 JSON 读取到正式 MaterialMap 安装完成的数值计算耗时 |
| `audit_out.py` | 独立核查实体域、拓扑、网格统计、Z 状态与物性有效性 |
| `refresh_viewer_outputs.py` | 刷新解析器工程预览 CSS/JS |

案例管道默认调用 `pcbkit-rust`、`pcbeq-rust`。输出目录与管理边界见[输出与发布](../docs/decisions/output-publication.md)。

`sync_out.py` 默认同步全部已发现的案例。指定 `--case NAME` 可仅同步一个案例，并刷新 `out/index.html` 中的完整产物索引。`NAME` 使用案例目录名、带扩展名的顶层归档名，或 `kicad-positive/native/<归档名不含扩展名>`、`kicad-positive/clean/<归档名不含扩展名>`。

```powershell
uv run python tools/sync_out.py --case minimal-test-odb --force
uv run python tools/sync_out.py --force
```

以上分别强制重建单个案例和全部案例。省略 `--force` 时会复用有效缓存。`--prune` 用于全量同步，会清理 `out/` 中未匹配到已发现案例的条目，和 `--case` 互斥。

`run_pipeline.py` 和 `sync_out.py` 支持 `--per-layer-png`，将逐层 PNG 随解析器预览发布到 `pcbkit/layers/`。

Python 等效管线通过 `PCBEQ_EXE` 或 `PATH` 定位独立的 `pcbeq`。`run_pipeline.py --pcbeq-exe` 指定外层 `pcbeq-rust` 命令，`verify_distributions.py --pcbeq-exe` 指定内层原生 `pcbeq` 二进制文件。

基准默认预热一次，每轮启动独立计算进程。从 Rust 内部 JSON 读取和校验计时，包含几何规划、采样、等效和 MaterialMap 写入关闭及发布操作；子进程墙钟时间另行记录。导入、预热、诊断报告序列化、图片和物理磁盘落盘不计入 MaterialMap 耗时。按任务生成的基准结果和审计报告写入 `.scratch/YYYY-MM-DD-<task-slug>/`。
