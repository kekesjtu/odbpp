# 维护工具

从仓库根目录使用 `uv run python tools/<脚本> --help` 查看输入和参数。

| 工具 | 用途 |
|---|---|
| `run_pipeline.py` | 通过两个独立 CLI 串联 ODB++ → JSON → MaterialMap |
| `sync_out.py` | 同步本产品线案例、缓存与图库 |
| `sync_contract.py` | 同步本分支中间 JSON 模型与 schema |
| `verify_distributions.py` | 独立安装与 sdist 重建验证 |
| `benchmark_pcbeq.py` | 仅测 JSON 读取至 MaterialMap 关闭，不含图片 |
| `audit_out.py` | 核查物性、局部网格、采样与实体域 |
| `refresh_viewer_outputs.py` | 刷新解析器工程预览 CSS/JS |

案例管道默认调用 `pcbkit-rust`、`pcbeq-rust`。目录与清单见[输出与发布](../docs/decisions/output-publication.md)。

基准默认预热一次。每轮从 JSON 读取和校验计时，包含几何规划、采样、等效和 MaterialMap 写入关闭及发布操作。导入、预热、诊断报告序列化、图片和物理磁盘落盘不计入 MaterialMap 耗时。基准结果可写到任务专属 `tmp/YYYY-MM-DD-slug/`。
