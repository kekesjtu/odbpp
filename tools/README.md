# 维护工具

使用 `uv run python tools/<脚本> --help` 查看输入输出。

| 工具 | 用途 |
|---|---|
| `run_pipeline.py` | 通过两个独立 CLI 串联 ODB++ → JSON → MaterialMap |
| `sync_out.py` | 同步本产品线案例、缓存与图库 |
| `sync_contract.py` | 同步本分支中间 JSON 模型与 schema |
| `verify_distributions.py` | 独立安装与 sdist 重建验证 |
| `benchmark_pcbeq.py` | 仅测 JSON 读取至 MaterialMap 关闭，不含图片 |
| `audit_out.py` | 核查物性、局部网格、采样与实体域 |
| `refresh_viewer_outputs.py` | 刷新解析器工程预览 CSS/JS |

本分支默认调用 `pcbkit-rust`、`pcbeq-rust`，产物位于 `out/<case>/rust/`。
性能测试只包含中间 JSON 读取、验证、几何、材料采样和 MaterialMap 文件关闭，不包含图片、进程启动与基准元数据。
测试和探索产物使用任务专属 `tmp/YYYY-MM-DD-slug/`。
